# DSM 运维笔记

> 面向「黑群晖救回来之后怎么日常用」的短笔记。只记录踩过的坑和验证过的做法。

---

## 1. 先分清端口

| 端口 | 服务 | 说明 |
|---|---|---|
| 5000 | DSM Web（HTTP） | 管理界面 |
| 5001 | DSM Web（HTTPS） | 证书异常时先用 5000 |
| 80 | Web Station / nginx | 未建站时显示 `Your website is not set up yet`，**不是故障** |
| 443 | Web Station / 反向代理 | 视套件配置 |
| 22 | SSH / SFTP | DSM 7 默认 OpenSSH 8.2 |
| 7681 / 7080 / 7304 | RR 环境（仅引导阶段） | ttyd / HTTP 菜单 / DUFS |

> 打开 `http://NAS/` 看到占位页时，先加 `:5000` 再判断。

---

## 2. 时间与套件中心

### 2.1 症状

套件中心提示「连接失败，请检查网络和时间设置或参阅此文章」。

### 2.2 判断顺序

1. 确认 NAS 能解析 DNS：在网关/路由器上看 `:53` 有没有应答。
2. 确认 NAS 能建 TCP 443：看它有没有连 Synology 的服务器。
3. 看 DSM 当前时间：`date` 或页面响应头 `Date`。
4. 如果时间偏差以年计，基本就是它。

### 2.3 为什么 NTP 也救不了

ntpd 默认拒绝一次步进超过 1000 秒的偏差。时间停在 2017 年时，点「立即更新」会一直失败，UI 只给一个笼统的「没有网络或连接不稳定」。

### 2.4 处理

1. 控制面板 → 区域选项 → 时间。
2. 时区选 `(GMT+08:00) 北京 / 上海`。
3. **关闭**「与 NTP 服务器同步」。
4. 手动把日期改到当前，时间改到接近当前。
5. 应用。
6. 再启用 NTP，点「立即更新」验证。

根治：换主板 CR2032。否则每次彻底断电都会回到 2017。

---

## 3. SSH 免密登录

### 3.1 生成专用密钥

不要复用日常主密钥。给 NAS 单独生成一把：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_nas -C "laptop@nas" -N ""
```

### 3.2 写入 NAS

在 DSM 里确认用户属于 `administrators` 组，并且该用户允许 SSH。然后把公钥追加到：

```
/var/services/homes/<user>/.ssh/authorized_keys
```

权限：

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

### 3.3 本机配置

`~/.ssh/config`：

```sshconfig
Host nas
    HostName <hostname>.local
    User <user>
    IdentityFile ~/.ssh/id_ed25519_nas
    IdentitiesOnly yes
```

用 mDNS 名字而不是 IP：DSM 的地址由网关 DHCP 下发，网关重启可能换网段；`.local` 名字更稳。

### 3.4 撤销

- NAS：删掉 `authorized_keys` 里对应那一行。
- 本机：删掉私钥文件和 config 里的 `Host nas` 段。

---

## 4. SFTP

### 4.1 用 DSM 接口开启

不要在 `sshd_config` 里硬改。调用 DSM 的文件服务 API（等同于控制面板 → 文件服务 → FTP → 启用 SFTP）：

```
SYNO.Core.FileServ.FTP.SFTP
  method=set
  enable=true
```

DSM 会自己处理 sshd 重载和状态持久化。

### 4.2 两套路径（必踩）

| 场景 | 家目录 |
|---|---|
| SSH shell | `/volume1/homes/<user>` |
| SFTP / scp | `/homes/<user>` |

SFTP 看到的是共享文件夹视图，根目录下直接是 `homes`、`web`、`docker` 等共享名，没有 `/volume1` 这一层。

### 4.3 用法

```bash
scp ./file.zip nas:/homes/<user>/
sftp nas
```

OpenSSH 9 以后 `scp` 默认走 SFTP 协议，正好匹配。

### 4.4 权限面

SFTP 和 SSH 共用 22 端口、共用账号权限。给普通用户开 SSH 前，先在「用户 → 权限 → 允许 SSH」里确认范围。

---

## 5. sudo 免密：方便，但要知道代价

### 5.1 推荐做法

用 drop-in，不改 `/etc/sudoers` 本体：

```
/etc/sudoers.d/99-<user>-nopasswd
  <user> ALL=(ALL) NOPASSWD: ALL
```

权限 `root:root 440`。回滚只需删掉这个文件。

### 5.2 三个坑

1. **DSM 没有 `visudo`**。写错 sudoers 会把 sudo 锁死，没有语法校验的安全网。写入后用 `sudo -n true` 反向自证：能跑通说明解析没问题。
2. **sudo 会重置 PATH**。`sudo docker ps` 可能报 `command not found`，要用全路径（如 `/usr/local/bin/docker`）。
3. **DSM 大版本升级可能重置 `/etc`**。升级后跑一次 `sudo -n true`，不在就重写。

### 5.3 安全边界

「免密私钥 + 免密 root」意味着：拿到私钥文件的人等于拿到 NAS root，中间没有第二道密码。

更稳的组合：

- 给私钥加密码短语，用 ssh-agent 缓存；或
- 把 NOPASSWD 限制成命令白名单（如只放行 `docker`、`systemctl`），而不是 `ALL`。

---

## 6. Docker / 套件端口自查

黑群晖上跑容器很常见，但端口映射容易无意识地暴露服务：

```bash
sudo /usr/local/bin/docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

重点看：

- `0.0.0.0:3306`（MySQL）
- `0.0.0.0:6379`（Redis）
- `0.0.0.0:5432`（PostgreSQL）
- 其他数据库/管理面板端口

如果这些服务只需要被本机或其他容器访问：

- 映射改成 `127.0.0.1:3306:3306`；或
- 用 Docker 内部网络，不发布端口；或
- 在 DSM 防火墙里限制来源网段。

Redis 如果没有密码又绑在 `0.0.0.0`，在局域网内就是一个现成的高危入口。

---

## 7. 常用诊断命令

```bash
# 系统与型号
uname -a
cat /etc/synoinfo.conf | head
cat /proc/cmdline

# 磁盘 / 存储
lsblk -o NAME,SIZE,TYPE,MODEL,SERIAL
cat /proc/mdstat
df -h
sudo /usr/local/bin/docker ps

# 网络
ip -4 addr
ip route
cat /etc/resolv.conf

# 服务端口
ss -lntp | head -50

# 时间
date
sudo ntpq -p 2>/dev/null || true
```

> DSM 的 `sudo` 会重置 PATH；容器相关命令用 `/usr/local/bin/docker` 全路径。

---

## 8. 安全提醒

1. 不要把 `user-config.yml`（含 SN/MAC）、`dhcp_server.log`、DSM 账号密码提交到公开仓库。
2. 不要把私钥放进任何同步盘或 Git 仓库。
3. 临时 DHCP 服务用完立即停掉，避免和主网关抢答。
4. 远程访问优先走 VPN / Tailscale / ZeroTier，不要在公网直接暴露 5000/5001/22。
5. 开任何「免密」之前，先想清楚私钥文件的落盘位置和备份策略。

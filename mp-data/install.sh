#!/usr/bin/env bash
# 豆粕数据平台 · 服务器安装脚本
# 用法：sudo bash install.sh
set -euo pipefail

APP_DIR=/srv/mp-data
SERVICE_USER=mpdata
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 sudo 运行"
command -v python3 >/dev/null 2>&1 || die "缺少 python3，请先安装"

python3 - <<'PY' || die "需要 Python 3.8 或更高版本"
import sys
assert sys.version_info >= (3, 8), sys.version
PY

say "Python: $(python3 --version)"

# 1. 专用用户
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    say "用户 $SERVICE_USER 已存在"
else
    useradd -r -m -d "$APP_DIR" -s /usr/sbin/nologin "$SERVICE_USER"
    say "已创建用户 $SERVICE_USER"
fi

# 2. 目录
mkdir -p "$APP_DIR"/{app,db,raw,site,logs,backup}

# 3. 代码（总是覆盖）
install -m 644 "$SRC_DIR"/app/*.py "$APP_DIR"/app/
if compgen -G "$SRC_DIR/app/*.css" > /dev/null; then
    install -m 644 "$SRC_DIR"/app/*.css "$APP_DIR"/app/
fi
say "已部署代码（含样式表）"

# 3b. 静态样式（总是覆盖，style.css 随代码一起发布）
mkdir -p "$APP_DIR"/site
install -m 644 "$SRC_DIR"/site/style.css "$APP_DIR"/site/
say "已部署 site/style.css"

# 4. 配置（只在不存在时写入，避免覆盖线上修改）
if [[ -f "$APP_DIR/app/config.json" ]]; then
    say "config.json 已存在，保留不覆盖"
else
    install -m 644 "$SRC_DIR"/app/config.json "$APP_DIR"/app/
    say "已写入默认 config.json"
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

# 5. 告警通道配置（只在不存在时写入模板，真实凭据请自行填写）
mkdir -p /etc/mp-data
if [[ -f /etc/mp-data/notify.json ]]; then
    say "notify.json 已存在，保留不覆盖"
else
    cat > /etc/mp-data/notify.json <<'JSON'
{
  "mail": {
    "enabled": false,
    "host": "smtp.qq.com",
    "port": 465,
    "user": "you@qq.com",
    "password": "SMTP授权码",
    "to": ["you@qq.com"]
  },
  "serverchan": {"enabled": false, "sendkey": ""}
}
JSON
    say "已写入告警配置模板 /etc/mp-data/notify.json（默认关闭，填好后把 enabled 改 true）"
fi
chown "$SERVICE_USER":"$SERVICE_USER" /etc/mp-data/notify.json
chmod 600 /etc/mp-data/notify.json

# 告警：金十 token（只在不存在时写入占位）
if [[ -f /etc/mp-data/jin10.json ]]; then
    say "jin10.json 已存在，保留不覆盖"
else
    echo '{"token": ""}' > /etc/mp-data/jin10.json
    say "已写入金十 token 占位文件（填 token 后资讯才会采集）"
fi
chown "$SERVICE_USER":"$SERVICE_USER" /etc/mp-data/jin10.json
chmod 600 /etc/mp-data/jin10.json

# 简报的 AI 摘要（OpenAI 兼容接口，默认 DeepSeek）
if [[ -f /etc/mp-data/llm.json ]]; then
    say "llm.json 已存在，保留不覆盖"
else
    cat > /etc/mp-data/llm.json <<'JSON'
{"api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}
JSON
    say "已写入 LLM 占位文件（填 api_key 后简报才有 AI 导语）"
fi
chown "$SERVICE_USER":"$SERVICE_USER" /etc/mp-data/llm.json
chmod 600 /etc/mp-data/llm.json

# 5. systemd
install -m 644 "$SRC_DIR"/deploy/mp-data.service        /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data.timer          /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data-backup.service /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data-backup.timer   /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data-api.service    /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data-brief.service  /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data-brief.timer    /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data-news.service   /etc/systemd/system/
install -m 644 "$SRC_DIR"/deploy/mp-data-news.timer     /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mp-data.timer
systemctl enable --now mp-data-backup.timer
systemctl enable --now mp-data-api.service
systemctl enable --now mp-data-brief.timer
systemctl enable --now mp-data-news.timer
say "已启用定时任务与写入接口（127.0.0.1:8765）"

# 6. 首次运行
say "首次抓取（可能耗时，失败不影响安装）"
if ! sudo -u "$SERVICE_USER" python3 "$APP_DIR/app/fetch.py"; then
    say "首次抓取失败，请稍后查看 $APP_DIR/logs/fetch.log"
fi
sudo -u "$SERVICE_USER" python3 "$APP_DIR/app/render.py" || true

cat <<EOF

────────────────────────────────────────────
安装完成。

数据目录：$APP_DIR
页面目录：$APP_DIR/site
日志：    $APP_DIR/logs/fetch.log

下一步：
1. 录入现货价：  sudo -u $SERVICE_USER python3 $APP_DIR/app/manual.py set A4.spot <价格>
2. 录入库存：    sudo -u $SERVICE_USER python3 $APP_DIR/app/manual.py set B1.inventory <万吨>
3. 查看定时器：  systemctl list-timers 'mp-data*'
4. 配置 Nginx：  把 $APP_DIR/site 指向 data.你的域名
────────────────────────────────────────────
EOF

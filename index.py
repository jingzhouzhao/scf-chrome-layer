import os
import sys
import json
import ssl
import urllib.request
import urllib.parse
import http.cookiejar
from datetime import datetime
from zoneinfo import ZoneInfo

if "/opt" not in sys.path:
    sys.path.insert(0, "/opt")

# ==================== 配置区域 ====================
DINGTALK_WEBHOOK = os.environ.get("DINGTALK_WEBHOOK")

RESULT_URL = "https://rsda.shrc.com.cn/selectFilePerson/selectPersonList.ftl"
QUERY_URL  = "https://rsda.shrc.com.cn/selectFilePerson/selectFilePersonListAction.action"
NOTIFY_METHOD = "dingtalk"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": RESULT_URL,
    "Origin": "https://rsda.shrc.com.cn",
}

# ==================== 工具函数 ====================
def _ssl_ctx():
    # 目标政务站点使用旧版 TLS/legacy 加密套件，默认 SECLEVEL=2 会握手失败，需降到 1
    ctx = ssl._create_unverified_context()
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=1")
    except Exception:
        pass
    return ctx


def query(id_num):
    cj = http.cookiejar.CookieJar()
    ctx = _ssl_ctx()
    handlers = [
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx),
    ]
    # 目标站点屏蔽了腾讯云出口 IP，可设置 HTTPS_PROXY/HTTP_PROXY 走允许的代理
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        print("使用代理:", proxy)
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    op = urllib.request.build_opener(*handlers)
    print("开始请求查询接口...")
    params = {
        "filePerson.person.identityCard": id_num,
        "isNumber": "0",
        "sub": "查询",
        "filePerson.nicetySel": "0",
    }
    # 表单数据必须按 GBK 编码（服务端是 GB2312 页面）
    body = "&".join(
        f"{k}={urllib.parse.quote(str(v).encode('gbk'))}" for k, v in params.items()
    )
    req = urllib.request.Request(
        QUERY_URL, data=body.encode("ascii"), headers=HEADERS, method="POST"
    )
    try:
        raw = op.open(req, timeout=30).read()  # 30s < 函数超时，确保能抛出真实错误
        print("请求完成，返回长度", len(raw))
    except Exception as e:
        print("请求异常:", repr(e))
        raise
    return raw.decode("gb18030", errors="replace")


def mask_id(s):
    if len(s) <= 8:
        return "x" * len(s)
    return s[:4] + "x" * (len(s) - 8) + s[-4:]


def send_dingtalk(message):
    if not DINGTALK_WEBHOOK:
        print("未配置 DINGTALK_WEBHOOK 环境变量，跳过钉钉推送")
        return False
    data = {
        "msgtype": "markdown",
        "markdown": {"title": "人事档案查询通知", "text": "【通知】" + message},
    }
    try:
        req = urllib.request.Request(
            DINGTALK_WEBHOOK,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, context=_ssl_ctx()) as r:
            res = json.loads(r.read().decode("utf-8"))
        if res.get("errcode") == 0:
            print("钉钉消息发送成功")
            return True
        print("钉钉消息发送失败:", res.get("errmsg"))
        return False
    except Exception as e:
        print("发送钉钉消息异常:", e)
        return False


# ==================== 主函数 ====================
def main_handler(event, context):
    id_num = os.environ.get("IDENTITY_CARD") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not id_num:
        return {"statusCode": 400, "body": json.dumps({"error": "身份证号未配置"})}

    m = mask_id(id_num)
    print("查询:", m)

    try:
        html = query(id_num)
    except Exception as e:
        if NOTIFY_METHOD == "dingtalk":
            send_dingtalk(f"查询异常：{e}")
        return {"statusCode": 500, "body": json.dumps({"success": False, "error": str(e)})}

    not_found_markers = ["未查询到相关信息", "没有找到", "不存在", "错误"]
    found_markers = ["档案", "列表"]
    is_not_found = any(k in html for k in not_found_markers)
    is_found = any(k in html for k in found_markers)
    is_success = is_found and not is_not_found

    if is_success:
        result_message = f"查询成功！身份证号：{m}。"
    else:
        result_message = f"查询失败！身份证号：{m}，未找到档案信息。"

    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    msg = f"""
📋 人事档案查询结果通知
━━━━━━━━━━━━━━━━━━━
🔍 查询号码：{m}
🔇 查询状态：{'成功' if is_success else '失败'}
{'✅' if is_success else '❌'} 详细信息：{result_message}
⏰ 查询时间：{now}
━━━━━━━━━━━━━━━━━━━
"""
    if NOTIFY_METHOD == "dingtalk":
        send_dingtalk(msg)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "success": True,
                "message": result_message,
            }
        ),
    }


if __name__ == "__main__":
    print(json.dumps(main_handler({}, {}), indent=2, ensure_ascii=False))

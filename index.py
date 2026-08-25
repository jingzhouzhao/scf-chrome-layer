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
DINGTALK_WEBHOOK = os.environ.get(
    "DINGTALK_WEBHOOK",
    "https://oapi.dingtalk.com/robot/send?access_token=***REMOVED***"
)

RESULT_URL = "https://rsda.shrc.com.cn/selectFilePerson/selectPersonList.ftl"
QUERY_URL  = "https://rsda.shrc.com.cn/selectFilePerson/selectFilePersonListAction.action"
CACHE_FILE = "/tmp/last_spider_result.txt"
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
def query(id_num):
    cj = http.cookiejar.CookieJar()
    ctx = ssl._create_unverified_context()  # 规避 SCF 可能的证书/握手卡顿
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx),
    )
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
        with urllib.request.urlopen(req) as r:
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
    id_num = os.environ.get("IDENTITY_CARD", "")
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

    cache_data = {"page_content": "", "first": "", "last": "", "last_success": False}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    cache_data.update(json.loads(content))
        except Exception as e:
            print("读取缓存失败:", e)

    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    has_changed = html != cache_data.get("page_content", "")
    is_first = not cache_data.get("page_content")

    if has_changed or is_first:
        msg = f"""
📋 人事档案查询结果通知
━━━━━━━━━━━━━━━━━━━━
🔍 查询号码：{m}
🔇 查询状态：{'成功' if is_success else '失败'}
{'✅' if is_success else '❌'} 详细信息：{result_message}
⏰ 查询时间：{now}
━━━━━━━━━━━━━━━━━━━━
"""
        if NOTIFY_METHOD == "dingtalk":
            send_dingtalk(msg)
        cache_data["page_content"] = html
        cache_data["first"] = cache_data.get("first") or now
        cache_data["last"] = now
        cache_data["last_success"] = is_success
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("更新缓存失败:", e)
    else:
        cache_data["last"] = now
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("更新缓存失败:", e)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "success": True,
                "message": result_message,
                "changed": has_changed or is_first,
            }
        ),
    }


if __name__ == "__main__":
    os.environ["IDENTITY_CARD"] = "34082719800101001X"
    print(json.dumps(main_handler({}, {}), indent=2, ensure_ascii=False))

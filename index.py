import os
import sys
import shutil

# 兜底：确保层挂载目录 /opt 在 Python 搜索路径中（不同运行时 PYTHONPATH 行为不一致）
if "/opt" not in sys.path:
    sys.path.insert(0, "/opt")
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==================== 配置区域 ====================
# 钉钉机器人Webhook URL（替换为您自己的Webhook地址）
DINGTALK_WEBHOOK = os.environ.get(
    "DINGTALK_WEBHOOK",
    "https://oapi.dingtalk.com/robot/send?access_token=***REMOVED***"
)

# 邮件配置（如果选择邮件方式，需要配置SMTP信息）
SMTP_SERVER = "smtp.qq.com"  # SMTP服务器地址
SMTP_PORT = 465              # SMTP端口
SMTP_USER = "your_email@qq.com"  # 发送邮箱
SMTP_PASS = "your_authorization_code"  # 授权码（不是登录密码）
EMAIL_TO = "recipient@example.com"  # 收件人邮箱

# 查询配置
RESULT_URL = 'https://rsda.shrc.com.cn/selectFilePerson/selectPersonList.ftl'
BUSINESS_NAME = 'sh_renshidangan'  # 业务配置名称
HEADLESS = True  # 无头模式，SCF环境必须启用

# 缓存文件路径（SCF环境可写目录）
CACHE_FILE = "/tmp/last_spider_result.json"

# 通知方式选择：'dingtalk' 或 'email'
NOTIFY_METHOD = 'dingtalk'

# 层挂载在 /opt 下：chrome/ 为浏览器和驱动
# SCF 的 /opt 只读，层里的可执行文件解压后可能没有 +x 位；
# 因此把 chrome/chromedriver 复制到可写的 /tmp 再赋执行权限，彻底避开只读问题。
_CHROME_SRC = "/opt/chrome/chrome"
_CHROMEDRIVER_SRC = "/opt/chrome/chromedriver"
_TMP_BIN = "/tmp/scf_chrome_bin"
os.makedirs(_TMP_BIN, exist_ok=True)
CHROME_BIN = os.path.join(_TMP_BIN, "chrome")
CHROMEDRIVER_BIN = os.path.join(_TMP_BIN, "chromedriver")

def _stage(exec_src, exec_dst):
    # 源文件比已暂存的新才重新复制
    if not os.path.exists(exec_dst) or os.path.getmtime(exec_src) > os.path.getmtime(exec_dst):
        shutil.copy(exec_src, exec_dst)
    os.chmod(exec_dst, 0o755)

_stage(_CHROME_SRC, CHROME_BIN)
_stage(_CHROMEDRIVER_SRC, CHROMEDRIVER_BIN)

# 把层里打包的依赖库目录加入动态链接器搜索路径（SCF CentOS7 基础镜像缺这些库）
os.environ["LD_LIBRARY_PATH"] = "/opt/chrome/lib" + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
# 给 headless chrome 一个可写的 HOME，避免创建 profile 失败
os.environ["HOME"] = "/tmp"

# ==================== 工具函数 ====================
def get_element_full_text(browser, element):
    """
    使用 JavaScript 获取元素及其所有子元素的完整文本内容
    """
    js_get_text = """
    var result = [];
    function traverse(node) {
      // 如果是文本节点且内容不为空，则收集
      if (node.nodeType === 3 && node.textContent.trim() !== '') {
        result.push(node.textContent.trim());
      }
      // 如果是元素节点，递归遍历子节点
      else if (node.nodeType === 1) {
        for (var i = 0; i < node.childNodes.length; i++) {
          traverse(node.childNodes[i]);
        }
        // 处理 input、textarea 等表单元素的 value 值
        if (node.value && typeof node.value === 'string' && node.value.trim() !== '') {
          result.push(node.value.trim());
        }
      }
    }
    traverse(arguments[0]);
    
    // 过滤空字符串并合并
    var filtered = [];
    for (var j = 0; j < result.length; j++) {
      if (result[j] !== '') {
        filtered.push(result[j]);
      }
    }
    return filtered.join('\\n').trim();
    """

    try:
        full_text = browser.execute_script(js_get_text, element)
        return full_text
    except Exception as e:
        print(f"获取元素完整文本失败：{e}")
        return element.text

def mask_id_number(id_num: str) -> str:
    """
    身份证号码脱敏处理，只显示前 4 位和后 4 位
    """
    if len(id_num) <= 8:
        return 'x' * len(id_num)
    return id_num[:4] + 'x' * (len(id_num) - 8) + id_num[-4:]

def send_dingtalk_message(message: str) -> bool:
    """
    发送钉钉机器人消息
    """
    try:
        # 构建钉钉消息（markdown格式）
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": "人事档案查询通知",
                "text": '【通知】'+message
            }
        }
        
        # 转换为JSON字符串
        json_data = json.dumps(data).encode('utf-8')
        
        # 创建请求
        req = urllib.request.Request(
            DINGTALK_WEBHOOK,
            data=json_data,
            headers={'Content-Type': 'application/json'}
        )
        
        # 发送请求
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            
        if result.get('errcode') == 0:
            print("钉钉消息发送成功")
            return True
        else:
            print(f"钉钉消息发送失败: {result.get('errmsg')}")
            return False
            
    except Exception as e:
        print(f"发送钉钉消息异常: {e}")
        return False

def send_email_notification(subject: str, body: str) -> bool:
    """
    发送邮件通知
    """
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header
        
        # 创建邮件内容
        message = MIMEText(body, 'plain', 'utf-8')
        message['From'] = Header(f"人事档案查询系统", 'utf-8')
        message['To'] = Header(EMAIL_TO, 'utf-8')
        message['Subject'] = Header(subject, 'utf-8')
        
        # 创建SMTP连接
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SMTP_USER, SMTP_PASS)
        
        # 发送邮件
        server.sendmail(SMTP_USER, [EMAIL_TO], message.as_string())
        server.quit()
        
        print("邮件发送成功")
        return True
        
    except Exception as e:
        print(f"发送邮件失败: {e}")
        return False

def send_notification(message: str, is_error: bool = False) -> bool:
    """
    发送通知（根据配置选择钉钉或邮件）
    """
    if NOTIFY_METHOD == 'dingtalk':
        return send_dingtalk_message(message)
    elif NOTIFY_METHOD == 'email':
        subject = "人事档案查询异常通知" if is_error else "人事档案查询结果通知"
        return send_email_notification(subject, message)
    else:
        print("不支持的通知方式")
        return False

# ==================== 主函数 ====================
def main_handler(event, context):
    """
    SCF云函数入口函数
    """
    # 从环境变量获取身份证号（SCF环境变量配置）
    id_num = os.environ.get('IDENTITY_CARD', '')
    
    if not id_num:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "身份证号未配置"})
        }
    
    masked_id_num = mask_id_number(id_num)
    
    print(f"📋 开始执行查询...")
    print(f"🔍 身份证号：{masked_id_num}")
    print(f"🌐 无头模式：{HEADLESS}")
    
    # 配置Chrome选项
    options = Options()
    if HEADLESS:
        options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    # 指定 Linux 版 Chrome 二进制（来自层 /opt/chrome/chrome）
    options.binary_location = CHROME_BIN

    # chrome/chromedriver 已在模块加载时复制到 /tmp 并赋 +x，此处无需再处理

    # 已显式指定 chromedriver 路径，关闭 Selenium Manager 避免它再去找/下载驱动
    os.environ["SE_ENABLE_MANAGER"] = "false"

    browser = None
    try:
        # 初始化浏览器（显式指定驱动路径，来自层 /opt/chrome/chromedriver）
        browser = webdriver.Chrome(options=options, service=Service(CHROMEDRIVER_BIN))
        
        # 设置页面加载超时和隐式等待
        browser.set_page_load_timeout(30)
        browser.implicitly_wait(10)
        
        # 访问查询页面
        browser.get(RESULT_URL)
        
        # 等待身份证号输入框可交互
        print("等待身份证输入框可交互...")
        id_num_input = WebDriverWait(browser, 15).until(
            EC.element_to_be_clickable((By.ID, 'identityCard'))
        )
        id_num_input.clear()
        id_num_input.click()
        id_num_input.send_keys(id_num)
        print(f"已输入身份证号：{masked_id_num}")
        
        # 等待提交按钮可交互
        print("等待提交按钮可交互...")
        submit_input = WebDriverWait(browser, 15).until(
            EC.element_to_be_clickable((By.XPATH,
                                        '/html/body/table/tbody/tr[2]/td/table/tbody/tr[2]/td/table/tbody/tr/td/form/table/tbody/tr[2]/td/input'))
        )
        submit_input.click()
        print("已点击提交按钮")
        
        # 等待页面加载
        time.sleep(10)
        
        # 获取页面内容
        current_url = browser.current_url
        page_source = browser.page_source
        
        # 判断查询结果
        is_success = False
        result_message = ""
        
        try:
            # 尝试查找成功页面的特征元素
            success_element = browser.find_element(by=By.XPATH, value='/html/body/table/tbody/tr/td')
            success_text = get_element_full_text(browser, success_element)
            page_source = success_text
            
            if success_text and ('档案' in success_text or '列表' in success_text or len(success_text) > 50):
                is_success = True
                result_message = f"查询成功！身份证号：{masked_id_num}，{success_text}。"
            else:
                fail_element = browser.find_element(by=By.XPATH, value='/html/body/table/tbody/tr[1]')
                fail_text = get_element_full_text(browser, fail_element)
                result_message = f"查询失败！身份证号：{masked_id_num}，未找到档案信息。详细信息：{fail_text}"
        except Exception as e:
            if '没有找到' in page_source or '不存在' in page_source or '错误' in page_source:
                result_message = f"查询失败！身份证号：{masked_id_num}，未找到档案信息。"
            else:
                result_message = f"查询成功！身份证号：{masked_id_num}，找到相关档案信息。"
                is_success = True
        
        # 当前页面文本作为缓存依据
        current_page_text = page_source
        
        # 加载上次的爬取结果
        cache_data = {
            "page_content": "",
            "first_crawl_date": "",
            "last_update_date": "",
            "last_success": False
        }
        
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        cache_data = json.loads(content)
            except Exception as e:
                print(f"读取缓存文件失败：{e}")
        
        # 获取当前时间（使用上海时区）
        current_time = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        
        # 判断页面内容是否发生变化
        has_changed = (current_page_text != cache_data.get("page_content", ""))
        is_first_crawl = not cache_data.get("page_content")
        
        print(f"页面内容变化：{'是' if has_changed else '否'}")
        print(f"首次爬取：{'是' if is_first_crawl else '否'}")
        
        # 只有在内容变化或首次爬取时才发送成功消息
        if has_changed or is_first_crawl:
            # 构建消息内容
            message_content = f"""
📋 人事档案查询结果通知
━━━━━━━━━━━━━━━━━━━━
🔍 查询号码：{masked_id_num}
🔇 查询状态：{'成功' if is_success else '失败'}
{'✅' if is_success else '❌'} 详细信息：{result_message}
⏰ 查询时间：{current_time}
━━━━━━━━━━━━━━━━━━━━
"""
            
            # 发送通知
            send_success = send_notification(message_content, is_error=False)
            
            if send_success:
                print("通知发送成功")
            else:
                print("通知发送失败")
            
            # 更新缓存数据
            cache_data["page_content"] = current_page_text
            cache_data["first_crawl_date"] = cache_data.get("first_crawl_date", current_time)
            cache_data["last_update_date"] = current_time
            cache_data["last_success"] = is_success
            
            # 保存到缓存文件
            try:
                with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
                print(f"缓存文件已更新 - 首次爬取：{cache_data['first_crawl_date']}, 最后更新：{current_time}")
            except Exception as e:
                print(f"更新缓存文件失败：{e}")
        else:
            # 内容无变化，不发送消息，但更新缓存时间
            cache_data["last_update_date"] = current_time
            cache_data["last_success"] = is_success
            
            try:
                with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
                print(f"页面内容无变化，无需发送通知 - 最后检查时间已更新：{current_time}")
            except Exception as e:
                print(f"更新缓存文件失败：{e}")
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "success": True,
                "message": "查询执行完成",
                "result": result_message,
                "changed": has_changed or is_first_crawl
            })
        }
        
    except Exception as e:
        # 发生异常时发送错误消息
        error_message = f"""
🚨 人事档案查询异常通知
━━━━━━━━━━━━━━━━━━━━
❌ 异常状态：爬虫执行失败
🔍 查询号码：{masked_id_num}
⚠️ 错误信息：{str(e)}
⏰ 异常时间：{datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")}
━━━━━━━━━━━━━━━━━━━━
"""
        print(f"发生异常：{e}")
        
        # 发送错误通知
        send_notification(error_message, is_error=True)
        
        return {
            "statusCode": 500,
            "body": json.dumps({
                "success": False,
                "error": str(e)
            })
        }
        
    finally:
        # 确保浏览器关闭
        if browser:
            try:
                browser.quit()
                print("浏览器已关闭")
            except:
                pass

# ==================== 本地测试 ====================
if __name__ == "__main__":
    # 本地测试时，设置环境变量
    os.environ['IDENTITY_CARD'] = '34082719800101001X'  # 替换为测试身份证号
    
    # 模拟SCF事件和上下文
    test_event = {
        "test": "event"
    }
    test_context = {
        "function_name": "spider_test",
        "function_version": "1.0.0"
    }
    
    # 执行主函数
    result = main_handler(test_event, test_context)
    print("\n执行结果:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

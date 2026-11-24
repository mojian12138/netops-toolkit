"""H3C 专属：AP 注册配置生成、开局/常用脚本生成、维保查询。"""

from __future__ import annotations

from netops.core.contracts import NavGroup, Stage, register_tool
from netops.tools.base import PlaceholderTool
from netops.ui import icons

_AP_SCRIPT = """#
# H3C AP 注册配置（AC 侧）
# 生成时间：2026-06-18 10:24:31
#
wlan ap office-3f-01 model WA6320
 serial-id 210235A1B2C3D4E5F6
 radio 1
  radio enable
  service-template 10
 radio 2
  radio enable
  service-template 11
#
wlan ap office-3f-02 model WA6320
 serial-id 210235A1B2C3D4E5F7
#

# 若输入了 AP 组，自动追加：
wlan ap-group office-3f
 ap office-3f-01
 ap office-3f-02"""

_OPENING_SCRIPT = """#
# 开局配置 · 华三 Comware V7
# 设备：S5560X-30C-EI / 主机名：CORE-SW-01
#
sysname CORE-SW-01
#
telnet server disable
ssh server enable
#
vlan 10 20 30
#
interface GigabitEthernet1/0/1
 description TO-CORE-SW-02-GE1/0/1
 port link-type trunk
 port trunk permit vlan 10 20 30
#
line vty 0 63
 authentication-mode scheme
 user-role network-admin
#
save force"""

_COMMON_SCRIPTS = """#
# 常用脚本 · 批量自动端口描述（H3C）
#
system-view
interface range GigabitEthernet1/0/1 to GigabitEthernet1/0/24
 description AUTO-ACCESS-{vlan}-{floor}
 quit
#
# 常用脚本 · 批量 AP 改名与分组（H3C）
#
wlan ap-group floor-3f
 ap rename office-3f-01 to ap-3f-001
 ap rename office-3f-02 to ap-3f-002
#
# 常用脚本 · 定期修改密码（华为）
#
aaa
 local-user admin password cipher %^%#****%^%#
 local-user admin privilege level 15
 quit"""


@register_tool()
class ApConfigTool(PlaceholderTool):
    tool_id = "h3c.ap"
    title = "AP 注册配置生成"
    group = NavGroup.VENDOR[1]
    order = 501
    icon = icons.AP
    stage = Stage("P3", "已实现")
    subtitle = "填 AP 名称、序列号、型号与所属 AP 组，自动生成可直接粘贴的 H3C Comware 注册脚本。"
    features = [
        "单条生成与批量导入（CSV：名称 / SN / 型号 / AP 组）两种方式",
        "型号列表可维护，生成时自动校验型号与序列号格式",
        "可选 AP 组：自动追加 wlan ap-group 下的 ap 成员命令",
        "生成结果高亮显示、一键复制，或导出 .cfg 归档",
    ]
    roadmap = [
        "Jinja2 模板放 resources/templates/h3c/ap_register.j2，厂商差异改模板不改代码",
        "批量导入先做整表校验，任何一行不合法都不生成，避免半成品脚本",
    ]
    spec = [
        ("表单", "AP 名称、序列号、型号、AP 组、射频服务模板"),
        ("批量", "CSV 导入，表格内联编辑与错误行标红"),
        ("生成", "实时生成 Comware 脚本，右侧代码区带行号"),
        ("复制", "整段复制 / 仅复制 AP 组命令 / 导出文件"),
    ]
    preview_code = _AP_SCRIPT
    preview_filename = "ap-register.cfg"
    preview_language = "H3C Comware"

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.ap_config_page import ApConfigPage

        return ApConfigPage()


@register_tool()
class ScriptGeneratorTool(PlaceholderTool):
    tool_id = "h3c.scripts"
    title = "脚本生成器"
    group = NavGroup.VENDOR[1]
    order = 502
    icon = icons.SCRIPT
    stage = Stage("P3", "已实现")
    subtitle = "华为 / 华三双厂商开局脚本与常用脚本生成，代码区右键即可收藏当前配置、下次直接载入。"
    features = [
        "开局脚本模板：华为 VRP 与 H3C Comware 双套，变量表单驱动",
        "常用脚本：自动端口描述、批量改密码、AP 批量改名与分组",
        "代码区右键菜单「收藏当前配置」，存入 SQLite 收藏库",
        "收藏库按厂商 / 分类 / 关键词检索，一键载入回代码区继续改",
    ]
    roadmap = [
        "契约驱动：templates/*.j2 + 参数表单 schema，新增脚本 = 新增模板",
        "收藏库表 script_favorites(name, vendor, category, content, tags)",
        "代码区用 QPlainTextEdit + pygments 高亮，不引入 QScintilla",
    ]
    spec = [
        ("模板", "左侧树：厂商 → 场景 → 模板，选择后右侧出表单"),
        ("表单", "模板声明的变量自动生成输入控件"),
        ("代码", "只读预览 → 点「编辑」转可编辑，右键出收藏/载入"),
        ("收藏", "侧栏收藏库列表，双击载入内容到代码区"),
    ]
    preview_code = _OPENING_SCRIPT
    preview_filename = "opening.cfg"
    preview_language = "H3C Comware"

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.script_page import ScriptPage

        return ScriptPage(db=ctx.db)


@register_tool()
class CommonScriptsTool(PlaceholderTool):
    tool_id = "h3c.common"
    title = "常用配置脚本"
    group = NavGroup.VENDOR[1]
    order = 503
    icon = icons.SCRIPT
    stage = Stage("P3")
    subtitle = "端口描述、批量改密码、AP 改名分组等高频小脚本集合，参数化生成，右键收藏复用。"
    features = [
        "自动端口描述：按「楼层-用途-对端设备」规则批量生成 description",
        "批量改密码：按密码策略生成华为 / 华三两套命令",
        "AP 批量改名并分组：CSV 导入新名称，自动生成 rename 序列",
        "与脚本生成器共用一个收藏库，收藏的片段跨页面可见",
    ]
    roadmap = [
        "每个常用脚本一个 j2 模板 + 一个参数 schema，全部可在 UI 注册",
        "生成前做冲突检查：改名后重名、端口不存在等情况提前拦下",
    ]
    spec = [
        ("选择", "常用脚本清单，卡片式选择"),
        ("参数", "按脚本渲染不同参数表单"),
        ("生成", "右侧代码区实时刷新，支持编辑与收藏"),
        ("导出", "导出 .txt / .cfg，保留厂商文件头注释"),
    ]
    preview_code = _COMMON_SCRIPTS
    preview_filename = "common.cfg"
    preview_language = "H3C / Huawei"


@register_tool()
class WarrantyTool(PlaceholderTool):
    tool_id = "h3c.warranty"
    title = "H3C 维保查询"
    group = NavGroup.VENDOR[1]
    order = 504
    icon = icons.WARRANTY
    stage = Stage("P3", "已实现")
    subtitle = "按序列号查询设备维保到期状态，临期与过保高亮，提前安排续保。"
    features = [
        "支持粘贴多个序列号批量查询，也可单条查询，结果表格汇总",
        "查询间隔可调（默认 500ms），串行限速避免触发官方风控",
        "触发滑块验证码即熔断：提示并一键打开官方页面，验证后可重试失败项",
        "临期（90 天内）与已过保行高亮，点击行查看保修/合同明细，可导出 CSV",
    ]
    roadmap = [
        "查询结果缓存到本地 SQLite，避免短时间内重复消耗官方配额",
        "多产品候选（type=1）支持在界面上一键带 PN 精查",
        "导出 Markdown 续保清单",
    ]
    spec = [
        ("输入", "多行序列号，支持「SN 产品号」、# 注释与文件导入"),
        ("查询", "串行批量查询，逐行显示进度与失败原因"),
        ("验证码", "429 即熔断提示，跳转浏览器验证后重试失败项"),
        ("结果", "序列号、状态、型号、维保截止、剩余天数"),
        ("导出", "续保清单导出 CSV"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.warranty_page import WarrantyPage

        return WarrantyPage()

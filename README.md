# AI字幕生成(联动版·HymT2定制) (AutoSubv3 v3.6.0)

私有市场仓库：以 [ifsherlock/AutoSubv3 v3.5.61](https://github.com/ifsherlock/MoviePilot-Plugins) 为基底，
移植 [AutoSubv2](https://github.com/chhc007/moviepilot-plugin-autosubv2) 的 **Hy-MT2 专用字幕翻译方案**，
产出 chhc007 定制版。插件 ID / 类名保持 `AutoSubv3`，海拉鲁字幕大师插件（SubtitleManualUpload）的进程内联动
（`running_plugins.get('AutoSubv3')` 桥接）**零改动**。

## v3.6.0 定制说明（vs 原版 3.5.61）

- **新增 Hy-MT2 专用翻译方案**（开关 `use_hymt2`），移植自 AutoSubv2 v3.0.x：
  - 官方 Structured Data 2 模板结构化批量翻译（背景/待译分区 + 等量分隔符 + 结尾锚点）
  - 输出行数与输入**严格一致**，不会合译错位；缺行自动整批重试，仍缺失的行单行兜底（可关闭）
  - 自动剥离字幕样式标签（HTML / ASS）与空条目；输出**仅中文译文**替换原字幕 content，条目数与时间轴保持不变
  - 请求不传采样参数，使用模型（Modelfile/服务端）内置官方参数
- **Hy-MT2 独立客户端旁路**：api_key / api_url / model 独立配置，不走 v3 的 AI API 端点池；
  启用后再端点池未配置时也不会中断插件初始化（不配置端点池只影响非 Hy-MT2 翻译路径）。
- **配置项**（默认值严格同 AutoSubv2）：

  | 配置项 | 说明 | 默认值 |
  |--------|------|--------|
  | use_hymt2 | 启用 Hy-MT2 专用翻译方案 | 否 |
  | hy_mt2_key | API 密钥（Ollama 可任意填写） | 空 |
  | hy_mt2_url | API URL | http://192.168.123.146:11436 |
  | hy_mt2_model | 模型名称 | hymt2-q8 |
  | hy_mt2_batch_size | 每批翻译行数 | 20 |
  | hy_mt2_context_window | 上下文窗口大小 | 10 |
  | hy_mt2_max_retries | 批次重试次数 | 3 |
  | hy_mt2_fallback | 启用单行兜底 | 开 |

- 翻译入口 `_AutoSubv3__translate_zh_subtitle` 增加 hymt2 分支：启用时走 Hy-MT2 结构化流程并 return，否则完全走 v3 原逻辑。
- **未改动**：submit_tasks / tasks_payload / cancel_tasks / restart_tasks / _status_payload / get_api 等联动 API，
  AI API 多端点池、双语/纯中文输出、任务队列、目录监控等 v3 全部原有功能。

## 目录结构

```
moviepilot-plugin-autosubv3/
├── package.json            # 私有市场元数据（key: AutoSubv3）
└── plugins/autosubv3/      # 插件全量代码（v3.5.61 复制 + hymt2 定制）
    ├── __init__.py         # 插件主入口：init_plugin 读取 use_hymt2/hy_mt2_*
    ├── core/compat_methods.py        # _AutoSubv3__translate_zh_subtitle hymt2 分支
    ├── core/config_schema.py         # build_config_form 新增 hymt2 表单区（v-show 联动）
    └── translate/hymt2_translate.py  # ★ 新增：Hy-MT2 独立翻译模块（Hymt2Translator）
```

## Hy-MT2 部署示例（Ollama）

```bash
ollama pull hymt2  # 或自备 Q8 GGUF，创建 Modelfile 命名为 hymt2-q8
ollama serve       # 默认 11436 端口（自定义端口则改 hy_mt2_url）
```

插件设置中：开启「外语翻译成中文」→ 开启「启用Hy-MT2专用翻译方案」→ 填写密钥（Ollama 任意）与 URL/模型 → 保存。

## 构建 & 验证

```bash
python -m py_compile plugins/autosubv3/**/*.py plugins/autosubv3/*.py
python - <<'PY'
import ast, pathlib
for p in pathlib.Path('plugins/autosubv3').rglob('*.py'):
    ast.parse(p.read_text(encoding='utf-8'))
print('all ast ok')
PY
```

## 版本历史

- **v3.6.0**：定制版 —— 移植 AutoSubv2 Hy-MT2 专用翻译方案（详见上文）
- v3.5.61：修复 V2 依赖清单 UTF-8 BOM 导致 iso639 无法识别（ifsherlock 原版）
- ...（此前版本均同 ifsherlock 原版 3.5.60 及更早）

## 致谢

- 原版作者 [ifsherlock](https://github.com/ifsherlock)
- AutoSubv2 作者 TimoYoung / chhc007（Hy-MT2 方案来源）
- 上游 autosub 系列插件作者
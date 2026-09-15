# -*- coding: utf-8 -*-
"""
Hy-MT2 专用字幕翻译方案（独立旁路模块）

移植自 AutoSubv2 (v3.0.x) 的 Hy-MT2 翻译逻辑（官方 Structured Data 2 模板）：

- 官方模板结构化批量翻译：背景/待译文本分区 + 等量分隔符 + 结尾锚点
- 输出行数与输入严格一致，不会合译错位
- 自动剥离字幕样式标签（HTML / ASS 标签）与空条目
- 批次解析缺行时自动整批重试；仍缺失的行做单行兜底补译（可关闭）
- 输出仅中文，替换原字幕 content，条目数与时间轴保持不变（不做 merge/整句合并）
- 请求不传采样参数，使用模型自身（Modelfile/服务端）内置的官方参数
- 独立持有 OpenAI 兼容客户端（api_key/api_url/model），与 v3 端点池体系完全解耦

本模块顶层仅使用标准库（re/time/typing），srt 读写通过注入的 load_srt/save_srt
回调完成，OpenAI 客户端延迟导入 —— 保证无第三方依赖时也可独立导入与单测。
"""
import re
import time
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "Hymt2Translator",
    "clean_text",
    "build_prompt",
    "parse",
    "clean_backfill",
]


# -------------------- 纯函数（模块级，亦绑定为类静态方法） --------------------


def clean_text(content: str) -> str:
    """剥离HTML(<font>)与ass({\\an2})样式标签，压平换行"""
    text = re.sub(r"<[^>]+>", "", content)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = text.replace("\n", " ").replace("\\N", " ")
    return re.sub(r"\s+", " ", text).strip()


def build_prompt(batch_texts: List[str], ctx_texts: List[str], start_no: int) -> str:
    """官方 Structured Data 2(背景/待译分区) + Delimiters(等量分隔符) + 结尾锚点"""
    ctx_text = "\n".join(ctx_texts)
    num_text = "\n".join(f"{start_no + k}. {t}" for k, t in enumerate(batch_texts))
    anchor = (f"（以上共 {len(batch_texts)} 行待译，必须逐行输出全部 {len(batch_texts)} 条译文，"
              f"从 {start_no} 行开始顺序编号，不得遗漏任何一行）")
    return ("【背景信息】\n" + ctx_text + "\n"
            "请结合背景信息将以下文本准确翻译为中文。你必须在译文中保留等量的分隔符，"
            "绝对不可遗漏、转义或翻译该符号，并注意分隔符的位置。\n"
            "【待翻译文本】\n" + num_text + "\n" + anchor)


def parse(raw: str, start_no: int, count: int) -> Dict[int, str]:
    """解析模型输出的编号行译文：'N. 译文'"""
    got = {}
    pat = re.compile(r"^\s*(\d+)\s*[.、:：]\s*(.+)$", re.M)
    for m in pat.finditer(raw):
        kid = int(m.group(1))
        text = m.group(2).strip()
        if text and start_no <= kid < start_no + count:
            got[kid] = text
    return got


def clean_backfill(out: str) -> str:
    """清洗单行兜底输出：取【待翻译】标记后内容，多行取最后一段，去常见前缀"""
    if "【待翻译】" in out:
        out = out.split("【待翻译】")[-1]
    parts = [l.strip() for l in out.split("\n") if l.strip()]
    if not parts:
        return ""
    t = parts[-1]
    t = re.sub(r"^(译文|翻译)[:：]\s*", "", t)
    t = re.sub(r"^\d+[.、:：]\s*", "", t)
    t = re.sub(r'^["\'“]|["\'”]$', "", t)
    return t.strip()


# -------------------- 翻译器主类 --------------------


class Hymt2Translator:
    """
    Hy-MT2 专用结构化字幕翻译器。

    独立持有一个 OpenAI 兼容客户端（api_key/api_url/model 独立，不走 v3 端点池），
    作为翻译旁路运行；批次/上下文/重试/单行兜底参数可配置。
    """

    def __init__(
        self,
        api_key: str,
        api_url: str,
        model: str = "hymt2-q8",
        batch_size: int = 20,
        context_window: int = 10,
        max_retries: int = 3,
        fallback: bool = True,
        logger: Any = None,
        compatible: bool = False,
        client: Optional[Any] = None,
        client_factory: Optional[Callable[[], Any]] = None,
    ):
        self._api_key = api_key
        self._api_url = api_url
        self._model = model or "hymt2-q8"
        self._batch_size = int(batch_size) if batch_size else 20
        self._context_window = int(context_window) if context_window else 10
        self._max_retries = int(max_retries) if max_retries else 3
        self._fallback = bool(fallback)
        self._logger = logger
        self._compatible = bool(compatible)
        self._client = client
        self._client_factory = client_factory
        # 单次请求重试次数（指数退避），与 v2 __hymt2_chat 的 3 次一致
        self.request_retries = 3
        self.stats = {'batches': 0, 'first_ok': 0, 'retry_ok': 0, 'backfill': 0, 'missing': 0}

    # ----- 客户端 -----
    def _ensure_client(self):
        """懒构建 OpenAI 兼容客户端：优先使用注入的 client/client_factory，否则延迟导入本包 OpenAi"""
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                from ..translate.openai_translate import OpenAi
                self._client = OpenAi(
                    api_key=self._api_key,
                    api_url=self._api_url,
                    proxy=None,
                    model=self._model,
                    compatible=self._compatible,
                    logger=self._logger,
                    endpoint_name="Hy-MT2",
                )
        return self._client

    # ----- 日志 -----
    def _info(self, message: str):
        cb = getattr(self._logger, "info", None)
        if cb:
            cb(message)

    def _warn(self, message: str):
        cb = getattr(self._logger, "warn", None) or getattr(self._logger, "warning", None)
        if cb:
            cb(message)

    def _error(self, message: str):
        cb = getattr(self._logger, "error", None)
        if cb:
            cb(message)

    # ----- 静态纯函数（镜像 v2 命名；实现复用模块级函数） -----

    @staticmethod
    def clean_text(content: str) -> str:
        return clean_text(content)

    @staticmethod
    def build_prompt(batch_texts: List[str], ctx_texts: List[str], start_no: int) -> str:
        return build_prompt(batch_texts, ctx_texts, start_no)

    @staticmethod
    def parse(raw: str, start_no: int, count: int) -> Dict[int, str]:
        return parse(raw, start_no, count)

    @staticmethod
    def clean_backfill(out: str) -> str:
        return clean_backfill(out)

    # ----- 请求 -----

    def chat(self, prompt: str) -> str:
        """直接调用模型(OpenAI兼容/v1)。不传采样参数——模型Modelfile已内置官方参数"""
        client = self._ensure_client()
        model = getattr(client, '_model', None) or "hymt2-q8"
        last_err = None
        for attempt in range(self.request_retries):
            try:
                resp = client.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}]
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                last_err = e
                self._warn(f"Hy-MT2请求失败(第{attempt + 1}次)：{e}")
                time.sleep(2 ** attempt)
        self._error(f"Hy-MT2请求失败：{last_err}")
        return ""

    def backfill(self, text: str, prev_texts: List[str]) -> str:
        """缺失行单行补译：优先纯单行(不回显前文)，空则退带前文版"""
        for _ in range(2):
            out = self.chat("将下面这行英文字幕翻译为简体中文，只输出译文本身，不要输出其他内容：\n" + text)
            t = self.clean_backfill(out)
            if t:
                return t
        prompt = ("参考以下前文保持连贯，把【待翻译】那一行字幕翻译成中文，只输出该行的译文本身：\n"
                  "【前文】\n" + "\n".join(prev_texts) + "\n【待翻译】\n" + text)
        return self.clean_backfill(self.chat(prompt))

    # ----- 主流程 -----

    def translate_zh_subtitle(
        self,
        source_subtitle: str,
        dest_subtitle: str,
        load_srt: Callable[[str], list],
        save_srt: Callable[[str, list], None],
    ):
        """
        Hy-MT2 结构化批量翻译：清洗→分批→官方模板→编号解析→缺行重试→单行兜底。

        输出仅中文译文替换原字幕 content，条目数与时间轴保持不变（不做merge/合并）。
        :param source_subtitle: 源字幕文件路径
        :param dest_subtitle: 目标字幕文件路径
        :param load_srt: 读取字幕回调 load_srt(path) -> List[Subtitle]
        :param save_srt: 写回字幕回调 save_srt(path, items)
        """
        subs = load_srt(source_subtitle)
        if not subs:
            self._warn("字幕文件为空，跳过翻译")
            save_srt(dest_subtitle, [])
            return
        # 数据清洗：空条目(清洗后为空)不参与翻译，输出时保留原content
        valid = []  # (subs索引, 清洗后文本)
        for i, item in enumerate(subs):
            text = self.clean_text(item.content)
            if text:
                valid.append((i, text))
        if not valid:
            self._warn("字幕内容全部为空，跳过翻译")
            save_srt(dest_subtitle, subs)
            return

        batch_size = self._batch_size
        ctx_win = self._context_window
        max_retries = self._max_retries
        use_fallback = self._fallback
        valid_texts = [t for _, t in valid]
        results = {}  # subs索引 -> 译文
        stats = {'batches': 0, 'first_ok': 0, 'retry_ok': 0, 'backfill': 0, 'missing': 0}
        start_t = time.time()

        for start in range(0, len(valid), batch_size):
            chunk = valid[start:start + batch_size]
            stats['batches'] += 1
            batch_texts = [t for _, t in chunk]
            ctx_b = valid_texts[max(0, start - ctx_win):start]
            ctx_a = valid_texts[start + len(chunk):start + len(chunk) + ctx_win]
            prompt = self.build_prompt(batch_texts, ctx_b + ctx_a, start + 1)

            got = {}
            attempt = 0
            for attempt in range(1, max_retries + 1):
                raw = self.chat(prompt)
                got = self.parse(raw, start + 1, len(chunk))
                if len(got) == len(chunk):
                    break
            if len(got) == len(chunk):
                if attempt == 1:
                    stats['first_ok'] += 1
                else:
                    stats['retry_ok'] += 1
            else:
                missing = [k for k in range(start + 1, start + len(chunk) + 1) if k not in got]
                if use_fallback:
                    for k in missing:
                        idx = start + (k - start - 1)
                        t = self.backfill(valid_texts[idx], valid_texts[max(0, idx - 5):idx])
                        if t:
                            got[k] = t
                            stats['backfill'] += 1
                still_missing = [k for k in range(start + 1, start + len(chunk) + 1) if k not in got]
                stats['missing'] += len(still_missing)
                if still_missing:
                    self._warn(f"Hy-MT2批次[{start + 1}-{start + len(chunk)}] "
                               f"重试{max_retries}次后仍有{len(still_missing)}行缺失: {still_missing}")
            # 写回该批译文（保持原字幕条目）
            for k, item in enumerate(chunk, start=start + 1):
                if k in got:
                    results[item[0]] = got[k]

        # 译文写回字幕(仅中文替换content)
        for i, item in enumerate(subs):
            if i in results:
                item.content = results[i]
        save_srt(dest_subtitle, subs)
        self.stats = dict(stats)
        elapsed = round(time.time() - start_t)
        self._info(f"Hy-MT2翻译完成：总批{stats['batches']}，一次过{stats['first_ok']}，"
                   f"重试后过{stats['retry_ok']}，单行兜底{stats['backfill']}，"
                   f"最终缺失{stats['missing']}，耗时{elapsed}秒")

    # v2 原名别名
    translate_zh_hymt2 = translate_zh_subtitle
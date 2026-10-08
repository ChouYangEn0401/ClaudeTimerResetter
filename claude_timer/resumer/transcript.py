# -*- coding: utf-8 -*-
"""transcript.py — 把 .jsonl 對話檔讀成「看得懂的結構」，而不是一長串字串。

預覽視窗真正的問題不是「有沒有把內容印出來」，是**印出來的東西讀不懂**：一個做了
三小時的對話裡，九成的記錄是工具往返（tool_use / tool_result）跟 thinking，把它們
原封不動接成一大段字串，人只會看到一面牆。

所以這裡做四件事，全部純讀本機檔案、不花任何 token：

    1. 分類    人講的話、Claude 的回覆、工具動作、思考、壓縮點，各自標成不同種類。
    2. 合併    連續的工具呼叫併成一則「⚙ Read ×3、Edit ×1」，不再一則一則洗版。
    3. 分輪    以「你說了一句話」為界切成一輪一輪（Turn）：你的 prompt → 中間過程 →
               它最後回的那一段。人認對話靠的就是「我當時說了什麼、它最後回了什麼」，
               中間幾十次工具往返預設收起來，要看再展開。（v1.3.0 之前是照時間差切段，
               結果一段裡還是一堆工具呼叫，等於沒切。）
    4. 摘要    整理出起訖時間、各類則數、最後一次你說了什麼 / 它回了什麼——
               挑對話時，這幾項幾乎就決定了「是不是這個」。

資料來源欄位（實測 Claude Code 2.1 的 .jsonl）：
    type=user/assistant  一般訊息；user 幾乎都是 tool_result，真人打字的只佔少數
    isMeta               系統塞的提醒，不是人打的，直接跳過
    isCompactSummary     壓縮後接上的摘要
    type=system + subtype=compact_boundary   對話被壓縮的分界點
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

KIND_HUMAN = "human"
KIND_ASSISTANT = "assistant"
KIND_TOOLS = "tools"
KIND_THINKING = "thinking"
KIND_SYSTEM = "system"

# 單則保留的字數上限（畫面上還會再摺疊）。
ENTRY_CHAR_CAP = 8000
# 整個對話最多保留幾則；超過就只留最近的，並在摘要裡註明。
MAX_ENTRIES = 3000
# 最多讀幾行 .jsonl，避免超大檔把記憶體吃光。
MAX_LINES = 80000

# 工具自己塞進訊息裡的區塊：系統提醒、VSCode 的「你現在開著哪個檔案」、診斷結果……
# 那些是給模型看的環境資訊，不是你打的字，留著會讓摘要跟輪次標題變成一堆標籤。
_NOISE_BLOCKS = re.compile(
    r"<(system-reminder|ide_[a-z_]+|diagnostics|local-command-stdout)>.*?</\1>", re.S)
# 訊息被截斷時會出現沒有收尾的殘缺區塊，至少把開頭那個標籤拿掉。
_OPEN_NOISE_TAG = re.compile(r"</?(system-reminder|ide_[a-z_]+|diagnostics)>")
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_COMMAND_TAGS = re.compile(r"</?command-[a-z-]+>|</?local-command-[a-z-]+>", re.S)
_BLANK_RUN = re.compile(r"\n{3,}")
# 以機器標籤開頭的行（<diagnostics>…、<ide_selection>… 之類）不適合當標題。
_TAG_LINE = re.compile(r"^<[A-Za-z][\w:-]*>")


@dataclass
class Entry:
    """畫面上的一則。tools 只有 KIND_TOOLS 用得到。"""

    index: int
    kind: str
    text: str
    ts: datetime | None = None
    tools: Counter = field(default_factory=Counter)

    @property
    def line_count(self) -> int:
        return self.text.count("\n") + 1

    def time_label(self) -> str:
        return self.ts.strftime("%H:%M") if self.ts else ""


@dataclass
class Turn:
    """一輪：你說一句 → 中間的過程 → 它最後回的那一段。

    prompt 可能是 None（例如對話檔開頭是壓縮摘要，或第一句之前只有分界）；
    reply 是這一輪**最後一則** Claude 的文字回覆；中間的工具、思考、中途的回覆、分界
    全部放 middle，畫面上預設收起來。
    """

    index: int
    prompt: Entry | None = None
    reply: Entry | None = None
    middle: list[Entry] = field(default_factory=list)

    @property
    def entries(self) -> list[Entry]:
        out: list[Entry] = []
        if self.prompt is not None:
            out.append(self.prompt)
        out.extend(self.middle)
        if self.reply is not None:
            out.append(self.reply)
        return out

    @property
    def start(self) -> datetime | None:
        for e in self.entries:
            if e.ts:
                return e.ts
        return None

    @property
    def end(self) -> datetime | None:
        for e in reversed(self.entries):
            if e.ts:
                return e.ts
        return None

    @property
    def title(self) -> str:
        """大綱上的一行：你說的第一句；沒有 prompt 就用分界或回覆。"""
        if self.prompt is not None:
            return topic_line(self.prompt.text, 60)
        for e in self.middle:
            if e.kind == KIND_SYSTEM:
                return topic_line(e.text, 60)
        if self.reply is not None:
            return topic_line(self.reply.text, 60)
        return "（只有工具動作）"

    def tool_counter(self) -> Counter:
        c: Counter = Counter()
        for e in self.middle:
            if e.kind == KIND_TOOLS:
                c.update(e.tools)
        return c

    def middle_label(self) -> str:
        """「中間過程」那一行要寫什麼：⚙ Bash ×12、Edit ×3｜回覆 2 則｜思考 1 則。"""
        if not self.middle:
            return ""
        bits: list[str] = []
        tools = self.tool_counter()
        if tools:
            bits.append(_tools_text(tools))
        c = Counter(e.kind for e in self.middle)
        if c[KIND_ASSISTANT]:
            bits.append(f"中途回覆 {c[KIND_ASSISTANT]} 則")
        if c[KIND_THINKING]:
            bits.append(f"思考 {c[KIND_THINKING]} 則")
        if c[KIND_SYSTEM]:
            bits.append(f"分界 {c[KIND_SYSTEM]}")
        return "｜".join(bits) or f"{len(self.middle)} 則"

    def header(self) -> str:
        start = self.start
        when = start.strftime("%m-%d %H:%M") if start else "時間不明"
        return f"第 {self.index + 1} 輪｜{when}"


@dataclass
class Digest:
    """挑對話時最先想知道的幾件事。"""

    started: datetime | None = None
    ended: datetime | None = None
    human: int = 0
    assistant: int = 0
    tool_calls: int = 0
    turns: int = 0
    top_tools: list[tuple[str, int]] = field(default_factory=list)
    opening: str = ""
    last_human: str = ""
    last_assistant: str = ""
    truncated: bool = False

    @property
    def duration(self) -> timedelta | None:
        if self.started and self.ended:
            return self.ended - self.started
        return None

    def span_label(self) -> str:
        if not self.started:
            return "沒有時間資訊"
        start = self.started.strftime("%Y-%m-%d %H:%M")
        end = self.ended.strftime("%m-%d %H:%M") if self.ended else "?"
        return f"{start} → {end}（歷時 {format_duration(self.duration)}）"

    def volume_label(self) -> str:
        bits = [f"{self.turns} 輪", f"你 {self.human} 則", f"Claude {self.assistant} 則"]
        if self.tool_calls:
            bits.append(f"工具 {self.tool_calls} 次")
        return "｜".join(bits)


@dataclass
class Transcript:
    ok: bool
    reason: str = ""
    entries: list[Entry] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    digest: Digest = field(default_factory=Digest)


# ---------- 對外 ----------

def read(jsonl_path: str | Path) -> Transcript:
    """讀一個對話檔，回傳結構化結果。任何讀取錯誤都回 ok=False，不丟例外。"""
    path = Path(jsonl_path)
    try:
        raw = _parse_file(path)
    except OSError as e:
        return Transcript(ok=False, reason=f"讀不到對話檔：{e}")
    if not raw.entries:
        return Transcript(ok=False, reason="這個對話檔裡沒有可以顯示的訊息內容")
    raw.turns = build_turns(raw.entries)
    raw.digest = _build_digest(raw.entries, turns=len(raw.turns), truncated=raw.digest.truncated)
    return raw


def build_turns(entries: list[Entry]) -> list[Turn]:
    """以「你說了一句話」為界切輪。每一輪的 reply 是最後一則 Claude 文字，其餘進 middle。"""
    turns: list[Turn] = []
    current: Turn | None = None
    for entry in entries:
        if entry.kind == KIND_HUMAN:
            if current is not None:
                turns.append(current)
            current = Turn(index=len(turns), prompt=entry)
            continue
        if current is None:
            current = Turn(index=0)
        if entry.kind == KIND_ASSISTANT:
            # 新的一則回覆出現，上一則就變成「中途回覆」
            if current.reply is not None:
                current.middle.append(current.reply)
            current.reply = entry
        else:
            # 工具／思考／分界：回覆之後還有工具動作，表示那一則回覆也只是中途的
            if current.reply is not None:
                current.middle.append(current.reply)
                current.reply = None
            current.middle.append(entry)
    if current is not None:
        turns.append(current)
    return turns


def format_duration(delta: timedelta | None) -> str:
    if delta is None:
        return "—"
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total} 秒"
    if total < 3600:
        return f"{total // 60} 分鐘"
    hours, minutes = divmod(total // 60, 60)
    if hours < 24:
        return f"{hours} 小時" + (f" {minutes} 分" if minutes else "")
    days, hours = divmod(hours, 24)
    return f"{days} 天" + (f" {hours} 小時" if hours else "")


def first_line(text: str, limit: int = 80) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:limit] + ("…" if len(line) > limit else "")
    return text.strip()[:limit]


def topic_line(text: str, limit: int = 60) -> str:
    """挑一行當標題用：跳過程式碼圍欄、標記、純符號那種看不出主題的行。"""
    for raw in text.splitlines():
        line = raw.strip().lstrip("#>-*` ").strip()
        if len(line) < 2 or line.startswith("```") or _TAG_LINE.match(line):
            continue
        return line[:limit] + ("…" if len(line) > limit else "")
    return first_line(text, limit)


def excerpt(text: str, limit: int = 240) -> str:
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


# ---------- 解析 ----------

def _parse_file(path: Path) -> Transcript:
    entries: list[Entry] = []
    truncated = False
    pending_tools: Entry | None = None   # 連續的工具呼叫併成同一則

    def push(entry: Entry) -> None:
        nonlocal pending_tools
        if entry.kind != KIND_TOOLS:
            pending_tools = None
        entry.index = len(entries)
        entries.append(entry)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i >= MAX_LINES:
                truncated = True
                break
            rec = _loads(line)
            if rec is None:
                continue
            rtype = rec.get("type")
            ts = _parse_ts(rec.get("timestamp"))

            if rtype == "system":
                if rec.get("subtype") == "compact_boundary":
                    push(Entry(0, KIND_SYSTEM, "對話在這裡被壓縮（compact），之前的內容換成一份摘要", ts))
                continue

            if rtype == "user":
                if rec.get("isMeta"):
                    continue          # 系統塞的提醒，不是人打的
                if rec.get("isCompactSummary"):
                    text = _clean(_collect_text(rec.get("message", {}).get("content")))
                    if text:
                        push(Entry(0, KIND_SYSTEM, "【壓縮摘要】\n" + text[:ENTRY_CHAR_CAP], ts))
                    continue
                text = _clean(_collect_text(rec.get("message", {}).get("content")))
                if text:
                    push(Entry(0, KIND_HUMAN, text[:ENTRY_CHAR_CAP], ts))
                continue

            if rtype != "assistant":
                continue

            content = rec.get("message", {}).get("content")
            if not isinstance(content, list):
                text = _clean(_collect_text(content))
                if text:
                    push(Entry(0, KIND_ASSISTANT, text[:ENTRY_CHAR_CAP], ts))
                continue

            texts, thinking, tools = _split_assistant_blocks(content)
            if thinking:
                push(Entry(0, KIND_THINKING, thinking[:ENTRY_CHAR_CAP], ts))
            if texts:
                push(Entry(0, KIND_ASSISTANT, _clean(texts)[:ENTRY_CHAR_CAP], ts))
            if tools:
                if pending_tools is not None:
                    pending_tools.tools.update(tools)
                    pending_tools.text = _tools_text(pending_tools.tools)
                else:
                    entry = Entry(0, KIND_TOOLS, "", ts)
                    entry.tools.update(tools)
                    entry.text = _tools_text(entry.tools)
                    push(entry)
                    pending_tools = entry

    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
        truncated = True
        for n, entry in enumerate(entries):
            entry.index = n

    t = Transcript(ok=True, entries=entries)
    t.digest.truncated = truncated
    return t


def _split_assistant_blocks(content: list) -> tuple[str, str, Counter]:
    texts: list[str] = []
    thinking: list[str] = []
    tools: Counter = Counter()
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and block.get("text"):
            texts.append(block["text"].strip())
        elif btype == "thinking" and (block.get("thinking") or "").strip():
            thinking.append(block["thinking"].strip())
        elif btype == "tool_use" and block.get("name"):
            tools[block["name"]] += 1
    return "\n\n".join(texts), "\n\n".join(thinking), tools


def _tools_text(tools: Counter) -> str:
    parts = [f"{name} ×{count}" if count > 1 else name for name, count in tools.most_common()]
    return "⚙ " + "、".join(parts)


def _collect_text(content) -> str:
    """只收真正是文字的部分；tool_result 那種一律不收（那是給模型看的，不是給人看的）。"""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            parts.append(block["text"].strip())
    return "\n\n".join(parts)


def _clean(text: str) -> str:
    """拿掉只有機器需要的標記，把 slash 指令還原成人看得懂的一行。"""
    if not text:
        return ""
    text = _NOISE_BLOCKS.sub("", text)
    text = _OPEN_NOISE_TAG.sub("", text)
    name = _COMMAND_NAME.search(text)
    if name:
        args = _COMMAND_ARGS.search(text)
        label = name.group(1).strip()
        extra = (args.group(1).strip() if args else "")
        return f"⌘ 執行指令 {label} {extra}".strip()
    text = _COMMAND_TAGS.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def _loads(line: str) -> dict | None:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def _parse_ts(raw) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


# ---------- 摘要 ----------

def _build_digest(entries: list[Entry], *, turns: int, truncated: bool) -> Digest:
    digest = Digest(truncated=truncated, turns=turns)
    stamps = [e.ts for e in entries if e.ts]
    if stamps:
        digest.started, digest.ended = stamps[0], stamps[-1]

    tools: Counter = Counter()
    for entry in entries:
        if entry.kind == KIND_HUMAN:
            digest.human += 1
            if not digest.opening:
                digest.opening = excerpt(entry.text, 200)
            digest.last_human = excerpt(entry.text, 300)
        elif entry.kind == KIND_ASSISTANT:
            digest.assistant += 1
            digest.last_assistant = excerpt(entry.text, 300)
        elif entry.kind == KIND_TOOLS:
            tools.update(entry.tools)

    digest.tool_calls = sum(tools.values())
    digest.top_tools = tools.most_common(5)
    return digest

# -*- coding: utf-8 -*-
"""app.py — ClaudeTimerResetter 的主控台（安裝 GUI）與三個進入點的分流。

同一支 exe 靠參數分流成三種身分——這是最容易誤會的一點：**「安裝器」跟「被排程叫起來
做事的那支程式」是同一個檔案**，安裝器安裝的其實就是它自己。

    (無參數)   開這個主控台：設定規則、選執行方式、安裝／解除安裝
    --tick     工作排程器到點叫的靜默進入點：有規則到期才刷新（見 service.run_tick_once）
    --tray     工具列常駐模式（見 tray.py）

它做的事：在你預期會重度使用 Claude 之前，先花約 $0.00025 打一次最便宜的探針，把 5 小時
額度視窗的重置時鐘提早啟動，免得工作到一半才撞牆、然後乾等視窗重置。
"""
from __future__ import annotations

import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from .. import __version__
from ..core import autostart, ping, rules as rules_mod
from ..paths import install_dir, rules_path, run_log
from ..ui import theme
from ..ui.scroll import ScrollableFrame
from ..ui.theme import COLORS
from . import service

INTRO = ("在你預期會重度使用 Claude「之前」，先打一次最便宜的探針（約 $0.00025），"
         "把 5 小時額度視窗的重置時鐘提早啟動——這樣等你真的用到額度快見底時，"
         "下一輪重置剛好接上，工作不會被打斷。")

# 規則型別：給人看的名字、一句說明、範例。rules.py 只認左邊那個英文 key。
RULE_TYPES = [
    ("daily_times", "每天固定幾個時間點（建議）",
     "最常用。自己算好一天要在哪幾點預熱，各帳號一組，例如上班前 2.5 小時開始、"
     "之後每 5 小時（視窗長度）一次。",
     "05:00, 10:00, 15:00, 20:00"),
    ("interval", "每隔 N 分鐘一次",
     "不管幾點，固定間隔就打一次。適合「整天都可能在用」的情況。", "480"),
    ("burst", "某個時刻之後連續重試",
     "指定時刻開始，每隔幾分鐘試一次，直到次數用完或成功為止。適合「那個時間點前後"
     "才會恢復」的情況。", "06:00 起每 15 分鐘，最多 5 次"),
]


class RuleDialog(tk.Toplevel):
    """新增一條排程規則。欄位依型別動態切換，下面即時顯示這條規則的白話意思。"""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.fonts = parent.fonts
        self.result: dict | None = None
        self.title("新增排程規則")
        self.configure(bg=COLORS["bg"])
        theme.fit_on_screen(self, 560, 560, 440, 400)
        self.transient(parent)

        scroller = ScrollableFrame(self, padding=(14, 12))
        scroller.pack(fill="both", expand=True)
        body = scroller.body

        self.type_var = tk.StringVar(value=RULE_TYPES[0][0])
        picker = ttk.Labelframe(body, text="規則類型", style="Card.TLabelframe", padding=10)
        picker.pack(fill="x")
        for key, label, note, example in RULE_TYPES:
            ttk.Radiobutton(picker, text=label, variable=self.type_var, value=key,
                            command=self._rebuild_fields).pack(anchor="w", pady=(4, 0))
            ttk.Label(picker, text=f"{note}\n例：{example}", style="Muted.TLabel",
                      wraplength=460, justify="left").pack(anchor="w", padx=(22, 0))

        self.fields = ttk.Labelframe(body, text="設定", style="Card.TLabelframe", padding=10)
        self.fields.pack(fill="x", pady=(12, 0))

        self.preview_var = tk.StringVar(value="")
        preview = ttk.Labelframe(body, text="這條規則的意思", style="Card.TLabelframe", padding=10)
        preview.pack(fill="x", pady=(12, 0))
        ttk.Label(preview, textvariable=self.preview_var, style="Card.TLabel",
                  wraplength=460, justify="left").pack(anchor="w")

        btns = ttk.Frame(self, padding=(14, 10))
        btns.pack(fill="x")
        ttk.Button(btns, text="加入", style="Accent.TButton", command=self._on_ok).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        self._rebuild_fields()
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _rebuild_fields(self) -> None:
        for w in self.fields.winfo_children():
            w.destroy()
        kind = self.type_var.get()

        def entry(row: int, label: str, var: tk.StringVar, width: int = 30, hint: str = "") -> None:
            ttk.Label(self.fields, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=4)
            e = ttk.Entry(self.fields, textvariable=var, width=width)
            e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=4)
            var.trace_add("write", lambda *_: self._refresh_preview())
            if hint:
                ttk.Label(self.fields, text=hint, style="Muted.TLabel", wraplength=420,
                          justify="left").grid(row=row + 1, column=1, sticky="w", padx=(8, 0))

        if kind == "daily_times":
            self.times_var = tk.StringVar(value="05:00, 10:00, 15:00, 20:00")
            entry(0, "每天這幾點：", self.times_var, 34, "HH:MM，用逗號分隔，可以填任意幾個時間點。")
        elif kind == "interval":
            self.minutes_var = tk.StringVar(value="480")
            entry(0, "每隔幾分鐘：", self.minutes_var, 10, "例如 480 就是每 8 小時一次。")
        else:
            self.start_var = tk.StringVar(value="06:00")
            self.interval_var = tk.StringVar(value="15")
            self.limit_var = tk.StringVar(value="5")
            self.stop_mode_var = tk.StringVar(value="count")
            entry(0, "從幾點開始：", self.start_var, 10)
            entry(2, "每隔幾分鐘重試：", self.interval_var, 10)
            ttk.Label(self.fields, text="停止條件：", style="Card.TLabel").grid(
                row=4, column=0, sticky="w", pady=4)
            box = ttk.Frame(self.fields, style="Card.TFrame")
            box.grid(row=4, column=1, sticky="w", padx=(8, 0))
            ttk.Radiobutton(box, text="重複固定次數", variable=self.stop_mode_var, value="count",
                            command=self._refresh_preview).pack(anchor="w")
            ttk.Radiobutton(box, text="直到成功為止", variable=self.stop_mode_var,
                            value="until_success", command=self._refresh_preview).pack(anchor="w")
            entry(5, "次數上限：", self.limit_var, 10,
                  "選「直到成功為止」時，這是防呆用的安全上限，避免探針一直失敗時整天無限重打。")
        self._refresh_preview()

    def _build_rule(self) -> dict | None:
        kind = self.type_var.get()
        try:
            if kind == "interval":
                minutes = int(self.minutes_var.get())
                if minutes < 1:
                    return None
                return {"id": rules_mod.new_id(), "type": "interval", "minutes": minutes}
            if kind == "daily_times":
                times = [x.strip() for x in self.times_var.get().split(",") if x.strip()]
                if not times:
                    return None
                for t in times:
                    datetime.strptime(t, "%H:%M")
                return {"id": rules_mod.new_id(), "type": "daily_times", "times": sorted(times)}
            datetime.strptime(self.start_var.get(), "%H:%M")
            interval = int(self.interval_var.get())
            limit = int(self.limit_var.get())
            if interval < 1 or limit < 1:
                return None
            stop = ({"mode": "count", "count": limit} if self.stop_mode_var.get() == "count"
                    else {"mode": "until_success", "max_attempts": limit})
            return {"id": rules_mod.new_id(), "type": "burst",
                    "start_time": self.start_var.get(), "interval_minutes": interval, "stop": stop}
        except ValueError:
            return None

    def _refresh_preview(self) -> None:
        rule = self._build_rule()
        self.preview_var.set(rules_mod.describe(rule) if rule else
                             "目前的輸入還不完整（時間用 HH:MM，數字要是正整數）。")

    def _on_ok(self) -> None:
        rule = self._build_rule()
        if rule is None:
            messagebox.showerror("輸入錯誤", "請確認欄位格式正確（時間用 HH:MM，數字要是正整數）",
                                 parent=self)
            return
        self.result = rule
        self.destroy()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.fonts = theme.apply(self)
        self.title(f"ClaudeTimerResetter 主控台 v{__version__}")
        self.configure(bg=COLORS["bg"])
        theme.fit_on_screen(self, 860, 860, 640, 480)
        self.rule_list: list[dict] = rules_mod.load_rules(rules_path())

        header = ttk.Frame(self, padding=(14, 12, 14, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Claude 額度提前重置排程", style="H1.TLabel").pack(anchor="w")
        ttk.Label(header, text=INTRO, style="MutedBg.TLabel", wraplength=780,
                  justify="left").pack(anchor="w", pady=(2, 0))

        scroller = ScrollableFrame(self, padding=(14, 8))
        scroller.pack(fill="both", expand=True)
        body = scroller.body

        self._build_status_card(body)
        self._build_rules_card(body)
        self._build_mode_card(body)
        self._build_action_card(body)
        self._build_log_card(body)

        bar = ttk.Frame(self, padding=(14, 8))
        bar.pack(fill="x")
        self.status_var = tk.StringVar(value="尚未測試")
        ttk.Label(bar, textvariable=self.status_var, style="MutedBg.TLabel",
                  wraplength=800, justify="left").pack(anchor="w")

        self._refresh_rule_list()
        self._refresh_status()
        self._refresh_log()

    # ---------- 版面 ----------

    def _card(self, parent, title: str) -> ttk.Labelframe:
        card = ttk.Labelframe(parent, text=title, style="Card.TLabelframe", padding=12)
        card.pack(fill="x", pady=(0, 12))
        return card

    def _build_status_card(self, parent) -> None:
        card = self._card(parent, "目前狀態")
        self.install_status_var = tk.StringVar(value="")
        ttk.Label(card, textvariable=self.install_status_var, style="Card.TLabel",
                  wraplength=780, justify="left").pack(anchor="w")

    def _build_rules_card(self, parent) -> None:
        card = self._card(parent, "① 什麼時候要刷新")
        ttk.Label(card, text="一條規則就是一組觸發時間。可以同時放好幾條，各自獨立判斷、互不影響。",
                  style="Muted.TLabel", wraplength=780, justify="left").pack(anchor="w")
        wrap = ttk.Frame(card, style="Card.TFrame")
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.rule_tree = ttk.Treeview(wrap, columns=("desc",), show="headings", height=6,
                                      selectmode="browse")
        self.rule_tree.heading("desc", text="規則")
        self.rule_tree.column("desc", width=640, anchor="w")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.rule_tree.yview)
        self.rule_tree.configure(yscrollcommand=bar.set)
        self.rule_tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="新增規則", command=self._add_rule).pack(side="left")
        ttk.Button(btns, text="刪除選取", command=self._remove_rule).pack(side="left", padx=(6, 0))
        self.rule_hint_var = tk.StringVar(value="")
        ttk.Label(btns, textvariable=self.rule_hint_var, style="Muted.TLabel").pack(side="right")

    def _build_mode_card(self, parent) -> None:
        card = self._card(parent, "② 用哪種方式在背景執行")
        self.mode_var = tk.StringVar(value=service.current_mode() or "scheduler")
        ttk.Radiobutton(card, text="Windows 工作排程器（建議）", variable=self.mode_var,
                        value="scheduler").pack(anchor="w")
        ttk.Label(card, text="到你設定的時間點才把程式叫起來跑一次，跑完就關。不用開著任何視窗，"
                             "也不需要系統管理員權限；沒有長命行程可以當掉，電腦睡眠錯過的時刻會盡快補跑。",
                  style="Muted.TLabel", wraplength=760, justify="left").pack(anchor="w", padx=(22, 0))
        ttk.Radiobutton(card, text="工具列常駐", variable=self.mode_var,
                        value="tray").pack(anchor="w", pady=(8, 0))
        ttk.Label(card, text="小圖示留在工具列，靠內部計時器到點觸發，右鍵可以看狀態、立即測試、結束。"
                             "缺點是它是單一長命行程，當掉就會停擺到下次重開——除非你就是想要一個看得見的圖示，"
                             "不然建議用上面那個。",
                  style="Muted.TLabel", wraplength=760, justify="left").pack(anchor="w", padx=(22, 0))
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(card, text="開機時自動啟動工具列常駐（只有選常駐模式才有意義）",
                        variable=self.autostart_var).pack(anchor="w", padx=(22, 0), pady=(6, 0))
        ttk.Label(card, text="兩種方式互斥：切換時安裝器會自動把另一種的殘留（排程項目 / 開機捷徑 / "
                             "常駐行程）清乾淨，不會兩邊同時打。",
                  style="Muted.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(8, 0))

    def _build_action_card(self, parent) -> None:
        card = self._card(parent, "③ 套用")
        btns = ttk.Frame(card, style="Card.TFrame")
        btns.pack(fill="x")
        ttk.Button(btns, text="安裝／更新設定", style="Accent.TButton",
                   command=self._install).pack(side="left")
        ttk.Button(btns, text="立即測試一次", command=self._test_now).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="解除安裝", style="Danger.TButton",
                   command=self._uninstall).pack(side="right")
        ttk.Label(card, text="「立即測試」走的是跟排程完全一樣的那條路：已經刷新過就不會再打，"
                             "所以按幾次都不會多花錢。",
                  style="Muted.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(8, 0))

    def _build_log_card(self, parent) -> None:
        card = self._card(parent, "最近執行紀錄")
        wrap = ttk.Frame(card, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(wrap, height=8, wrap="none", state="disabled", borderwidth=0,
                                highlightthickness=0, font=self.fonts.mono,
                                bg=COLORS["log_bg"], fg=COLORS["log_fg"])
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=bar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        foot = ttk.Frame(card, style="Card.TFrame")
        foot.pack(fill="x", pady=(6, 0))
        ttk.Label(foot, text=f"完整紀錄：{run_log()}", style="Muted.TLabel").pack(side="left")
        ttk.Button(foot, text="重新整理", command=self._refresh_log).pack(side="right")

    # ---------- 畫面更新 ----------

    def _refresh_status(self) -> None:
        self.install_status_var.set("\n".join(service.status_lines()))

    def _refresh_rule_list(self) -> None:
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, rule in enumerate(self.rule_list):
            self.rule_tree.insert("", "end", iid=str(i), values=(rules_mod.describe(rule),))
        self.rule_hint_var.set(
            "還沒有任何規則——按「新增規則」加一條" if not self.rule_list
            else f"共 {len(self.rule_list)} 條；改完記得按下面的「安裝／更新設定」才會生效")

    def _refresh_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, service.tail_log(20))
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    # ---------- 動作 ----------

    def _add_rule(self) -> None:
        dialog = RuleDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.rule_list.append(dialog.result)
            self._refresh_rule_list()

    def _remove_rule(self) -> None:
        sel = self.rule_tree.selection()
        if not sel:
            messagebox.showinfo("尚未選擇", "請先選一條規則", parent=self)
            return
        del self.rule_list[int(sel[0])]
        self._refresh_rule_list()

    def _test_now(self) -> None:
        self.status_var.set("測試中，請稍候（約 5 秒）…")
        self.update()
        result = ping.refresh(max_wait_seconds=0)
        if not result["ok"]:
            self.status_var.set(f"✗ {result['probe']['reason']}")
            return
        if result["action"] == "refreshed":
            p = result["probe"]
            self.status_var.set(f"✓ 已刷新｜模型 {p['model']}｜花費 ${p['cost_usd']:.5f}"
                                f"｜耗時 {p['seconds']}s")
        else:
            self.status_var.set(f"· 這次沒打（{result['action']}）：{result['reason']}")
        self._refresh_log()

    def _install(self) -> None:
        if not self.rule_list:
            messagebox.showwarning("尚未設定規則", "請先新增至少一條排程規則", parent=self)
            return
        if not service.is_frozen():
            messagebox.showerror(
                "開發模式",
                "現在是用原始碼跑的，安裝需要 exe。\n請先執行 scripts\\build.bat 打包成 exe 再安裝。",
                parent=self)
            return
        try:
            service.apply_install(self.rule_list, self.mode_var.get(), self.autostart_var.get())
        except Exception as e:
            messagebox.showerror("安裝失敗", str(e), parent=self)
            return
        messagebox.showinfo("完成", f"已套用設定。\n安裝位置：{install_dir()}", parent=self)
        self._refresh_status()
        self._refresh_log()

    def _uninstall(self) -> None:
        if not messagebox.askyesno(
            "確認解除安裝",
            "將移除工作排程項目、開機自動啟動捷徑、常駐行程，以及安裝資料夾裡的設定與紀錄。\n"
            "確定要繼續嗎？", parent=self,
        ):
            return
        service.apply_uninstall()  # 若現在跑的就是安裝目錄裡那份 exe，這裡不會返回
        messagebox.showinfo("完成", "已解除安裝", parent=self)
        self.destroy()


def launch_gui() -> None:
    App().mainloop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--tick" in argv:
        return service.run_tick_once()
    if "--tray" in argv:
        from . import tray
        tray.run()
        return 0
    launch_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())

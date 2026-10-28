"""Custom Tk approval desk launched as a companion window to the QtPy app."""
from __future__ import annotations

import argparse
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chief.approval_gate import ApprovalGate
from app.orchestrator.orchestrator import Orchestrator
from app.storage.database import Database


BG = "#0b1220"
PANEL = "#111c2f"
RAISED = "#18263d"
TEXT = "#edf3fb"
MUTED = "#9aaac0"
TEAL = "#38d6bd"
AMBER = "#f5bc58"
RED = "#ff727b"


class ApprovalDesk:
    def __init__(self, db_path: str):
        self.db = Database(db_path)
        self.orchestrator = Orchestrator(self.db)
        self.gate = ApprovalGate(self.db)
        self.records = {}
        self.root = tk.Tk()
        self.root.title("Representative approval desk | ITNI AI Team")
        self.root.geometry("1040x680")
        self.root.minsize(850, 560)
        self.root.configure(bg=BG)
        self._style()
        self._build()
        self.refresh()

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        rowheight=31, borderwidth=0, font=("Malgun Gothic", 10))
        style.configure("Treeview.Heading", background=RAISED, foreground=MUTED,
                        relief="flat", font=("Malgun Gothic", 9, "bold"))
        style.map("Treeview", background=[("selected", "#205b66")], foreground=[("selected", TEXT)])

    def _build(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=22, pady=(19, 12))
        tk.Label(header, text="대표 승인 작업대", bg=BG, fg=TEXT,
                 font=("Malgun Gothic", 20, "bold")).pack(anchor="w")
        tk.Label(header, text="클라우드 접근은 요청 범위를 확인하고 승인 또는 반려하세요.",
                 bg=BG, fg=MUTED, font=("Malgun Gothic", 10)).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(0, 15))
        left = tk.Frame(body, bg=PANEL, highlightbackground="#273750", highlightthickness=1)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=PANEL, width=320, highlightbackground="#273750", highlightthickness=1)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        cols = ("kind", "approval", "project", "summary", "status")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for col, label, width in (("kind", "요청 유형", 105), ("approval", "승인 ID", 130),
                                  ("project", "프로젝트", 130), ("summary", "요청 목적", 260),
                                  ("status", "상태", 110)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=78, stretch=col == "summary")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Double-1>", self.show_detail)

        tk.Label(right, text="요청 상세", bg=PANEL, fg=TEXT,
                 font=("Malgun Gothic", 12, "bold")).pack(anchor="w", padx=16, pady=(16, 8))
        self.detail = tk.Text(right, height=13, wrap="word", bg="#0c1728", fg=TEXT,
                              insertbackground=TEXT, relief="flat", padx=11, pady=10,
                              font=("Malgun Gothic", 10), state="disabled")
        self.detail.pack(fill="x", padx=14)
        tk.Label(right, text="대표자 메모", bg=PANEL, fg=MUTED,
                 font=("Malgun Gothic", 9, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        self.comment = tk.Text(right, height=4, wrap="word", bg="#0c1728", fg=TEXT,
                               insertbackground=TEXT, relief="flat", padx=10, pady=8,
                               font=("Malgun Gothic", 10))
        self.comment.pack(fill="x", padx=14)
        actions = tk.Frame(right, bg=PANEL)
        actions.pack(fill="x", padx=14, pady=14)
        tk.Button(actions, text="승인", command=lambda: self.decide("APPROVED"),
                  bg=TEAL, fg="#06221f", activebackground="#67e8d3", relief="flat",
                  font=("Malgun Gothic", 10, "bold"), padx=17, pady=9).pack(side="left", fill="x", expand=True)
        tk.Button(actions, text="반려", command=lambda: self.decide("REJECTED"),
                  bg="#4b2635", fg="#ffdfe2", activebackground="#663140", relief="flat",
                  font=("Malgun Gothic", 10, "bold"), padx=17, pady=9).pack(side="left", fill="x", expand=True, padx=(8, 0))
        tk.Button(right, text="새로고침", command=self.refresh, bg=RAISED, fg=TEXT,
                  activebackground="#263853", relief="flat", padx=12, pady=8).pack(fill="x", padx=14, pady=(0, 14))
        tk.Label(self.root, text="승인 기록은 요청된 프로젝트와 범위에 연결됩니다. Cloud Gateway 미구성 시 호출은 실행되지 않습니다.",
                 bg=BG, fg=MUTED, font=("Malgun Gothic", 9)).pack(anchor="w", padx=23, pady=(0, 13))

    def refresh(self):
        selected = self.tree.selection()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.records.clear()
        conn = self.db.connect()
        try:
            cloud = conn.execute("SELECT approval_id,project_id,purpose,scope,data_scope,estimated_cost,status,created_at,comment FROM model_approvals ORDER BY created_at DESC LIMIT 100").fetchall()
            final = conn.execute("SELECT approval_id,project_id,task_id,result_id,status,comment,created_at FROM approvals ORDER BY created_at DESC LIMIT 100").fetchall()
        finally:
            conn.close()
        for row in cloud:
            item_id = "cloud:" + row[0]
            self.records[item_id] = ("cloud", row)
            self.tree.insert("", "end", iid=item_id, values=("클라우드", row[0], row[1], row[2], row[6]))
        for row in final:
            item_id = "final:" + row[0]
            self.records[item_id] = ("final", row)
            self.tree.insert("", "end", iid=item_id, values=("최종 결과", row[0], row[1], "Task " + row[2], row[4]))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
            self.show_detail()
        elif self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])
            self.show_detail()
        else:
            self._set_detail("현재 승인 요청이 없습니다.")

    def show_detail(self, _event=None):
        selected = self.tree.selection()
        if not selected or selected[0] not in self.records:
            return
        kind, row = self.records[selected[0]]
        if kind == "cloud":
            approval_id, project_id, purpose, scope, data_scope, cost, status, created, comment = row
            details = (f"유형: 클라우드 AI 접근\n상태: {status}\n요청 ID: {approval_id}\n프로젝트: {project_id}\n\n"
                       f"목적\n{purpose}\n\n허용 요청 범위\n{scope}\n\n데이터 범위\n{data_scope or '미지정'}\n\n예상 비용\n{cost or '미지정'}\n\n요청 시각\n{created}")
            self._set_detail(details)
            self._set_comment(comment or "")
        else:
            approval_id, project_id, task_id, result_id, status, old_comment, created = row
            details = (f"유형: 최종 결과 승인\n상태: {status}\n요청 ID: {approval_id}\n프로젝트: {project_id}\n"
                       f"Task: {task_id}\nResult: {result_id}\n요청 시각: {created}")
            self._set_detail(details)
            self._set_comment(old_comment or "")

    def _set_detail(self, value):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", value)
        self.detail.configure(state="disabled")

    def _set_comment(self, value):
        self.comment.delete("1.0", "end")
        self.comment.insert("1.0", value)

    def decide(self, status):
        selected = self.tree.selection()
        if not selected or selected[0] not in self.records:
            messagebox.showinfo("요청 선택", "먼저 승인 요청을 선택하세요.", parent=self.root)
            return
        kind, row = self.records[selected[0]]
        if (kind == "cloud" and row[6] != "PENDING") or (kind == "final" and row[4] != "PENDING"):
            messagebox.showinfo("이미 처리된 요청", "대기 중인 요청만 결정할 수 있습니다.", parent=self.root)
            return
        comment = self.comment.get("1.0", "end").strip()
        try:
            if kind == "cloud":
                self.gate.decide(row[0], status, comment)
            else:
                self.orchestrator.approve(row[0], status, comment)
            messagebox.showinfo("처리 완료", "승인" if status == "APPROVED" else "반려", parent=self.root)
            self.refresh()
        except Exception as exc:
            messagebox.showerror("처리 실패", str(exc), parent=self.root)

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()
    ApprovalDesk(args.db_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


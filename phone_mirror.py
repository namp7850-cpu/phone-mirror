#!/usr/bin/env python3
"""
Phone Mirror - chiếu màn hình điện thoại Android lên laptop
-----------------------------------------------------------
- Dùng scrcpy (>= 2.0) làm lõi: độ trễ thấp (~35-70 ms), điều khiển bằng chuột.
- Cửa sổ có đúng tỷ lệ điện thoại, kéo thả bằng thanh tiêu đề, có thể luôn nằm trên cùng.
- Không cần thư viện Python ngoài (chỉ dùng tkinter có sẵn).

Cách đặt scrcpy: tải bản scrcpy cho Windows, giải nén rồi đặt file này
cạnh thư mục scrcpy (hoặc cho scrcpy vào PATH).
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

IS_WIN = sys.platform.startswith("win")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
EXE = ".exe" if IS_WIN else ""
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WINDOW_TITLE = "Phone Mirror"


def find_scrcpy():
    """Tìm scrcpy: cạnh file này, trong thư mục con, hoặc trong PATH."""
    p = os.path.join(APP_DIR, "scrcpy" + EXE)
    if os.path.isfile(p):
        return p
    try:
        for d in sorted(os.listdir(APP_DIR)):
            p = os.path.join(APP_DIR, d, "scrcpy" + EXE)
            if os.path.isfile(p):
                return p
    except OSError:
        pass
    return shutil.which("scrcpy")


def find_adb(scrcpy_path):
    """Ưu tiên adb nằm cùng thư mục scrcpy để tránh lệch phiên bản adb server."""
    if scrcpy_path:
        p = os.path.join(os.path.dirname(scrcpy_path), "adb" + EXE)
        if os.path.isfile(p):
            return p
    return shutil.which("adb")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(WINDOW_TITLE + " - Launcher")
        self.resizable(False, False)

        self.q = queue.Queue()
        self.proc = None
        self.devices = {}  # hiển thị -> serial

        self.scrcpy = find_scrcpy()
        self.adb = find_adb(self.scrcpy)

        self._build_ui()
        self.after(100, self._poll)

        if not self.scrcpy or not self.adb:
            self.log("Không tìm thấy scrcpy/adb. Tải scrcpy và đặt cạnh file này "
                     "(hoặc thêm vào PATH), rồi mở lại app.")
        else:
            self.log(f"scrcpy: {self.scrcpy}")
            self.refresh_devices()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        f = ttk.Frame(self)
        f.grid(sticky="nsew", padx=6, pady=6)

        # Thiết bị
        ttk.Label(f, text="Thiết bị:").grid(row=0, column=0, sticky="w", **pad)
        self.cb_dev = ttk.Combobox(f, state="readonly", width=38)
        self.cb_dev.grid(row=0, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(f, text="Làm mới", command=self.refresh_devices).grid(row=0, column=3, **pad)

        # Chất lượng
        ttk.Label(f, text="Độ phân giải (cạnh dài):").grid(row=1, column=0, sticky="w", **pad)
        self.cb_size = ttk.Combobox(f, state="readonly", width=10,
                                    values=["640", "800", "1024", "1280", "1920", "Gốc"])
        self.cb_size.set("1024")
        self.cb_size.grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(f, text="Bitrate (Mbps):").grid(row=1, column=2, sticky="e", **pad)
        self.cb_rate = ttk.Combobox(f, state="readonly", width=6, values=["4", "8", "12", "16"])
        self.cb_rate.set("8")
        self.cb_rate.grid(row=1, column=3, sticky="w", **pad)

        ttk.Label(f, text="FPS tối đa:").grid(row=2, column=0, sticky="w", **pad)
        self.cb_fps = ttk.Combobox(f, state="readonly", width=10, values=["30", "60", "90"])
        self.cb_fps.set("60")
        self.cb_fps.grid(row=2, column=1, sticky="w", **pad)

        # Tuỳ chọn
        self.v_top = tk.BooleanVar(value=True)
        self.v_ctrl = tk.BooleanVar(value=True)
        self.v_off = tk.BooleanVar(value=False)
        self.v_awake = tk.BooleanVar(value=True)
        self.v_audio = tk.BooleanVar(value=False)
        opts = [
            ("Luôn nằm trên cùng", self.v_top),
            ("Cho phép điều khiển bằng chuột", self.v_ctrl),
            ("Tắt màn hình điện thoại khi chiếu", self.v_off),
            ("Giữ điện thoại không ngủ", self.v_awake),
            ("Chuyển âm thanh sang laptop", self.v_audio),
        ]
        for i, (txt, var) in enumerate(opts):
            ttk.Checkbutton(f, text=txt, variable=var).grid(
                row=3 + i // 2, column=(i % 2) * 2, columnspan=2, sticky="w", **pad)

        # Nút chính
        bf = ttk.Frame(f)
        bf.grid(row=6, column=0, columnspan=4, sticky="we", pady=(8, 2))
        self.btn_start = ttk.Button(bf, text="▶ Bắt đầu chiếu", command=self.start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(bf, text="■ Dừng", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        ttk.Button(bf, text="USB → Wi-Fi", command=self.to_wifi).pack(side="left", padx=4)
        ttk.Button(bf, text="Kết nối IP", command=self.connect_ip).pack(side="left", padx=4)
        ttk.Button(bf, text="Ghép nối (Android 11+)", command=self.pair).pack(side="left", padx=4)

        # Log
        self.txt = tk.Text(f, height=9, width=70, state="disabled", bg="#111", fg="#ddd",
                           font=("Consolas", 9))
        self.txt.grid(row=7, column=0, columnspan=4, padx=8, pady=(6, 4))

    def log(self, msg):
        self.txt.configure(state="normal")
        self.txt.insert("end", msg.rstrip() + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    # ---------------- Hàng đợi cho thread ----------------
    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log(val)
                elif kind == "refresh":
                    self.refresh_devices()
                elif kind == "stopped":
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self.log(f"[scrcpy] đã thoát (mã {val})")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    # ---------------- ADB ----------------
    def run_adb(self, *args, timeout=15):
        try:
            r = subprocess.run([self.adb, *args], capture_output=True, text=True,
                               timeout=timeout, creationflags=NO_WINDOW,
                               encoding="utf-8", errors="replace")
            return r.returncode, (r.stdout + r.stderr).strip()
        except Exception as e:  # noqa: BLE001
            return 1, str(e)

    def refresh_devices(self):
        if not self.adb:
            return
        _, out = self.run_adb("devices", "-l")
        self.devices = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ("device", "unauthorized", "offline"):
                model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "")
                label = f"{parts[0]}  {model}  [{parts[1]}]"
                self.devices[label] = parts[0]
        labels = list(self.devices.keys())
        self.cb_dev["values"] = labels
        if labels:
            self.cb_dev.set(labels[0])
            if "unauthorized" in labels[0]:
                self.log("Điện thoại chưa cho phép: bấm 'Cho phép gỡ lỗi USB' trên màn hình điện thoại.")
        else:
            self.cb_dev.set("")
            self.log("Chưa thấy thiết bị. Cắm USB và bật Gỡ lỗi USB (hoặc dùng Wi-Fi).")

    def selected_serial(self):
        return self.devices.get(self.cb_dev.get())

    # ---------------- Chiếu ----------------
    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not self.scrcpy:
            messagebox.showerror(WINDOW_TITLE, "Chưa có scrcpy.")
            return
        serial = self.selected_serial()
        if not serial:
            messagebox.showwarning(WINDOW_TITLE, "Chưa chọn thiết bị.")
            return

        cmd = [self.scrcpy, "-s", serial,
               "--window-title", WINDOW_TITLE,
               f"--video-bit-rate={self.cb_rate.get()}M",
               f"--max-fps={self.cb_fps.get()}"]
        if self.cb_size.get() != "Gốc":
            cmd.append(f"--max-size={self.cb_size.get()}")
        if self.v_top.get():
            cmd.append("--always-on-top")
        if not self.v_ctrl.get():
            cmd.append("--no-control")
        if self.v_off.get():
            cmd.append("--turn-screen-off")
        if self.v_awake.get():
            cmd.append("--stay-awake")
        if not self.v_audio.get():
            cmd.append("--no-audio")

        env = os.environ.copy()
        if self.adb:
            env["ADB"] = self.adb
        self.log("> " + " ".join(cmd))
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace",
                                         creationflags=NO_WINDOW, env=env)
        except Exception as e:  # noqa: BLE001
            self.log(f"Không chạy được scrcpy: {e}")
            return
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.bg(lambda p=self.proc: self._pump(p))

    def _pump(self, proc):
        for line in proc.stdout:
            self.q.put(("log", line))
        proc.wait()
        self.q.put(("stopped", proc.returncode))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    # ---------------- Wi-Fi ----------------
    def to_wifi(self):
        serial = self.selected_serial()
        if not serial:
            messagebox.showwarning(WINDOW_TITLE, "Cắm USB và chọn thiết bị trước.")
            return
        if ":" in serial:
            self.log("Thiết bị này đã kết nối qua Wi-Fi.")
            return

        def job():
            _, out = self.run_adb("-s", serial, "shell", "ip", "-f", "inet", "addr", "show", "wlan0")
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out)
            if not m:
                self.q.put(("log", "Không lấy được IP Wi-Fi của điện thoại (đã bật Wi-Fi chưa?)."))
                return
            ip = m.group(1)
            self.q.put(("log", f"IP điện thoại: {ip}. Đang chuyển sang chế độ không dây..."))
            self.run_adb("-s", serial, "tcpip", "5555")
            time.sleep(2)
            _, out = self.run_adb("connect", f"{ip}:5555")
            self.q.put(("log", out))
            self.q.put(("log", "Xong. Có thể rút USB, rồi bấm Làm mới và chọn thiết bị mới."))
            self.q.put(("refresh", None))

        self.bg(job)

    def connect_ip(self):
        addr = simpledialog.askstring(WINDOW_TITLE, "Nhập IP:cổng (vd 192.168.1.20:5555):", parent=self)
        if not addr:
            return
        if ":" not in addr:
            addr += ":5555"

        def job():
            _, out = self.run_adb("connect", addr.strip())
            self.q.put(("log", out))
            self.q.put(("refresh", None))

        self.bg(job)

    def pair(self):
        addr = simpledialog.askstring(WINDOW_TITLE, "IP:cổng GHÉP NỐI (hiện trong Gỡ lỗi không dây):", parent=self)
        if not addr:
            return
        code = simpledialog.askstring(WINDOW_TITLE, "Mã ghép nối 6 số:", parent=self)
        if not code:
            return

        def job():
            _, out = self.run_adb("pair", addr.strip(), code.strip())
            self.q.put(("log", out))
            self.q.put(("log", "Sau khi ghép nối, dùng 'Kết nối IP' với IP:cổng ở màn hình Gỡ lỗi không dây."))

        self.bg(job)

    # ---------------- Thoát ----------------
    def on_close(self):
        self.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()

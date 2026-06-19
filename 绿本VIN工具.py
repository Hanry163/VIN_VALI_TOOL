#!/usr/bin/env python3
"""
绿本VIN识别+校验工具
功能:
  1. 打开PDF/JPG/PNG文件 → OCR识别VIN → 填入文本框
  2. 手动输入VIN
  3. 校验VIN校验位 → 显示详细结果

注意: 所有重量级import放文件顶部，避免PyInstaller解压DLL时卡住UI
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os, io, threading, time, json

# 图片和OCR提前加载，pymupdf放线程里（PyInstaller DLL加载问题）
from PIL import Image
from aip import AipOcr

# ============ 百度OCR配置 ============
APP_ID = '123759398'
API_KEY='JClEZDwwrMi8d9B2XyjqgK5H'
SECRET_KEY='gPe36L2RpMqHKkomQcpjfDwWbSvM98B1'
# ====================================

# ============ VIN校验 ============
VIN_CHAR_MAP = {
    '0':0,'1':1,'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,
    'A':1,'B':2,'C':3,'D':4,'E':5,'F':6,'G':7,'H':8,
    'J':1,'K':2,'L':3,'M':4,'N':5,'P':7,'R':9,
    'S':2,'T':3,'U':4,'V':5,'W':6,'X':7,'Y':8,'Z':9
}
WEIGHTS = [8,7,6,5,4,3,2,10,0,9,8,7,6,5,4,3,2]
INVALID_CHARS = {'I','O','Q'}

def validate_vin(vin):
    result = {'valid': False, 'vin': '', 'check_digit': '', 'calculated': '', 'errors': [], 'details': []}
    v = vin.strip().upper()
    result['vin'] = v
    if not v:
        result['errors'].append('VIN为空')
        return result
    if len(v) != 17:
        result['errors'].append(f'长度{len(v)}位，应为17位')
        return result
    for c in v:
        if c in INVALID_CHARS:
            result['errors'].append(f'含非法字符"{c}"（I/O/Q不允许）')
            return result
        if c not in VIN_CHAR_MAP:
            result['errors'].append(f'无法映射字符"{c}"')
            return result
    result['check_digit'] = v[8]
    total = 0
    for i, ch in enumerate(v):
        val = VIN_CHAR_MAP[ch]
        w = WEIGHTS[i]
        prod = val * w
        total += prod
        result['details'].append((i + 1, ch, val, w, prod))
    rem = total % 11
    calculated = str(rem) if rem < 10 else 'X'
    result['calculated'] = calculated
    result['valid'] = (calculated == result['check_digit'])
    result['total'] = total
    result['remainder'] = rem
    if not result['valid']:
        result['errors'].append(f'校验位不符：计算值"{calculated}"，实际值"{result["check_digit"]}"')
    return result
# ====================================


class VinToolApp:
    def __init__(self, root):
        self.root = root
        self.root.title('绿本 VIN 识别 + 校验工具 v0.5')
        self.root.geometry('680x520')
        self.root.resizable(False, False)
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f'+{x}+{y}')

        # 设置窗口图标（支持 PyInstaller 打包路径）
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            ico_base = sys._MEIPASS
        else:
            ico_base = os.path.dirname(__file__)
        ico_path = os.path.join(ico_base, 'ANTU_ICO.ico')
        if os.path.isfile(ico_path):
            try:
                self.root.iconbitmap(ico_path)
            except Exception:
                pass

        self.ocr_running = False
        self._stop_ocr = False

        # 提前初始化OCR客户端（避免线程中卡顿）
        self._ocr_client = None
        try:
            self._ocr_client = AipOcr(APP_ID, API_KEY, SECRET_KEY)
            # 设置超时60秒（在requests层面生效）
            self._ocr_client._timeout = 60
        except Exception as e:
            self._ocr_client = None
            self._ocr_init_error = str(e)
        else:
            self._ocr_init_error = None

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        # === 第1行：文件选择 + 识别按钮 ===
        row1 = ttk.Frame(main)
        row1.pack(fill=tk.X, pady=(0, 10))

        self.btn_open = ttk.Button(row1, text='📂 打开文件', command=self._open_file)
        self.btn_open.pack(side=tk.LEFT)

        self.file_path_var = tk.StringVar()
        self.entry_file_path = ttk.Entry(row1, textvariable=self.file_path_var,
                                         font=('Consolas', 9))
        self.entry_file_path.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        self.entry_file_path.bind('<KeyRelease>', self._on_path_changed)
        self.file_path_var.set('')

        self.btn_ocr = ttk.Button(row1, text='🔍 识别VIN', command=self._start_ocr, state=tk.DISABLED)
        self.btn_ocr.pack(side=tk.RIGHT, padx=(5, 0))

        # === 第2行：VIN输入 ===
        row2 = ttk.Frame(main)
        row2.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(row2, text='VIN码:', font=('Microsoft YaHei', 10, 'bold')).pack(side=tk.LEFT)

        self.vin_var = tk.StringVar()
        self.entry_vin = ttk.Entry(row2, textvariable=self.vin_var,
                                   font=('Consolas', 14, 'bold'), width=20)
        self.entry_vin.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)
        self.entry_vin.bind('<KeyRelease>', self._auto_upper)

        self.btn_validate = ttk.Button(row2, text='✅ 校验', command=self._validate, width=8)
        self.btn_validate.pack(side=tk.RIGHT)

        # === 第3行：校验结果 ===
        row3 = ttk.LabelFrame(main, text='校验结果', padding=10)
        row3.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        self.result_text = tk.Text(row3, font=('Consolas', 10), height=14,
                                   wrap=tk.WORD, relief=tk.FLAT, bg='#f5f5f5')
        self.result_text.pack(fill=tk.BOTH, expand=True)

        # 颜色标签
        self.result_text.tag_config('pass', foreground='green', font=('Consolas', 12, 'bold'))
        self.result_text.tag_config('fail', foreground='red', font=('Consolas', 12, 'bold'))
        self.result_text.tag_config('cancel', foreground='orange', font=('Consolas', 12, 'bold'))
        self.result_text.tag_config('label', foreground='#333', font=('Consolas', 10, 'bold'))
        self.result_text.tag_config('value', foreground='black')
        self.result_text.tag_config('error', foreground='red')
        self.result_text.tag_config('header', foreground='#666', font=('Consolas', 9))
        self.result_text.tag_config('vin_display', foreground='blue', font=('Consolas', 14, 'bold'))

        self._show_placeholder()

    def _show_placeholder(self):
        self.result_text.delete(1.0, tk.END)
        if self._ocr_init_error:
            self.result_text.insert(tk.END, f'⚠️ OCR初始化失败: {self._ocr_init_error}\n\n', 'fail')
        self.result_text.insert(tk.END, '输入VIN或打开文件识别后，点击"校验"查看结果\n\n', 'header')
        self.result_text.insert(tk.END, '操作说明:\n', 'label')
        self.result_text.insert(tk.END, '  📂 打开文件 → 选择PDF/JPG/PNG\n')
        self.result_text.insert(tk.END, '  🔍 识别VIN  → OCR识别VIN\n')
        self.result_text.insert(tk.END, '  ⏹ 停止      → 识别过程中可随时取消\n')
        self.result_text.insert(tk.END, '  ✅ 校验      → 校验VIN\n')
        self.result_text.insert(tk.END, '  也可直接在输入框内手动输入VIN\n')

    def _auto_upper(self, event):
        cursor = self.entry_vin.index(tk.INSERT)
        content = self.vin_var.get()
        upper = content.upper()
        if content != upper:
            self.vin_var.set(upper)
            self.entry_vin.icursor(cursor)

    def _on_path_changed(self, event=None):
        if self.file_path_var.get().strip():
            self.btn_ocr.config(state=tk.NORMAL)
        else:
            self.btn_ocr.config(state=tk.DISABLED)

    def _open_file(self):
        if self.ocr_running:
            return
        fpath = filedialog.askopenfilename(
            title='选择绿本文件',
            filetypes=[
                ('支持的文件', '*.pdf *.jpg *.jpeg *.png'),
                ('PDF文件', '*.pdf'),
                ('图片文件', '*.jpg *.jpeg *.png'),
                ('所有文件', '*.*')
            ]
        )
        if not fpath:
            return
        self.file_path_var.set(fpath)
        self.btn_ocr.config(state=tk.NORMAL)
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, f'已选择文件\n', 'label')
        self.result_text.insert(tk.END, '点击"识别VIN"开始OCR识别\n', 'header')

    # ---- 状态更新辅助（线程安全） ----
    def _status(self, msg):
        """线程安全地追加状态文字"""
        self.root.after(0, lambda: self.result_text.insert(tk.END, msg))

    def _status_replace(self, msg):
        """线程安全地替换全部文字（清空后写入）"""
        self.root.after(0, lambda: (
            self.result_text.delete(1.0, tk.END),
            self.result_text.insert(tk.END, msg)
        ))

    # ---- OCR流程 ----
    def _start_ocr(self):
        if self.ocr_running:
            return
        fpath = self.file_path_var.get().strip()
        if not fpath:
            messagebox.showwarning('提示', '请输入或选择文件路径')
            return
        if not os.path.isfile(fpath):
            messagebox.showerror('错误', f'文件不存在:\n{fpath}')
            return
        if self._ocr_client is None:
            messagebox.showerror('错误', f'OCR客户端初始化失败，无法识别\n{self._ocr_init_error}')
            return

        self._stop_ocr = False
        self.ocr_running = True
        self.btn_ocr.config(text='⏹ 停止', command=self._stop_ocr_now)
        self.btn_open.config(state=tk.DISABLED)
        self.btn_validate.config(state=tk.DISABLED)
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, '正在处理...\n', 'header')

        t = threading.Thread(target=self._do_ocr, daemon=True)
        t.start()

    def _stop_ocr_now(self):
        self._stop_ocr = True
        self.btn_ocr.config(text='⏹ 取消中...', state=tk.DISABLED)
        self.result_text.insert(tk.END, '\n⏹ 用户请求取消...\n', 'cancel')

    def _reset_ui(self):
        self.ocr_running = False
        self._stop_ocr = False
        self.btn_ocr.config(text='🔍 识别VIN', command=self._start_ocr, state=tk.NORMAL)
        self.btn_open.config(state=tk.NORMAL)
        self.btn_validate.config(state=tk.NORMAL)

    def _do_ocr(self):
        """OCR后台线程 — 所有重量级操作都在线程中执行"""
        try:
            fpath = self.file_path_var.get().strip()
            ext = os.path.splitext(fpath)[1].lower()

            if self._stop_ocr:
                self._status_replace('⏹ 已取消\n')
                self.root.after(0, self._reset_ui)
                return

            # ---- 渲染图像 ----
            self._status('加载PDF引擎...\n')
            t0 = time.time()
            if ext == '.pdf':
                image_data = self._pdf_to_image(fpath)
            else:
                image_data = self._load_image(fpath)
            render_ms = (time.time() - t0) * 1000

            if self._stop_ocr:
                self._status_replace('⏹ 已取消\n')
                self.root.after(0, self._reset_ui)
                return

            if image_data is None:
                self._status_replace('❌ 错误\n\n无法读取文件内容')
                self.root.after(0, self._reset_ui)
                return

            kb = len(image_data) / 1024
            self._status(f'图像大小: {kb:.0f}KB（渲染耗时 {render_ms:.0f}ms）\n')

            # ---- 调用百度OCR ----
            self._status('正在调用百度OCR（60秒超时）...\n')
            t1 = time.time()
            r = self._ocr_client.vehicle_registration_certificate(image_data)
            ocr_ms = (time.time() - t1) * 1000

            if self._stop_ocr:
                self._status_replace('⏹ 已取消\n')
                self.root.after(0, self._reset_ui)
                return

            if 'error_code' in r:
                err_msg = r.get('error_msg', '未知错误')
                err_code = r.get('error_code', '')
                self._status_replace(f'❌ OCR失败 (code={err_code})\n\n{err_msg}')
                self.root.after(0, self._reset_ui)
                return

            words_result = r.get('words_result', {})
            vin = words_result.get('vin', {}).get('words', '').strip()

            if vin:
                self._status(f'OCR耗时: {ocr_ms:.0f}ms\n')
                # 用 root.after 安全更新UI
                self.root.after(0, lambda v=vin: self._ocr_done(v))
            else:
                self._status_replace(
                    '❌ 未识别到VIN码\n\n'
                    '可能原因:\n'
                    '  - 文件不清晰\n'
                    '  - 不是绿本扫描件\n'
                    '  - 角度不对'
                )
                self.root.after(0, self._reset_ui)

        except Exception as e:
            estr = str(e)
            if 'timed out' in estr.lower() or 'timeout' in estr.lower():
                self._status_replace(
                    f'⏱ 请求超时\n\n'
                    f'错误: {e}\n\n'
                    '可能原因:\n'
                    '  - 网络连接不稳定\n'
                    '  - 百度API响应慢\n'
                    '  - 图片文件太大'
                )
            else:
                self._status_replace(f'❌ 处理出错\n\n{e}')
            self.root.after(0, self._reset_ui)

    def _pdf_to_image(self, fpath):
        # PyMuPDF延时导入（PyInstaller exe中DLL加载问题）
        import pymupdf
        doc = pymupdf.open(fpath)
        try:
            pages = min(doc.page_count, 3)
            for page_idx in range(pages):
                if self._stop_ocr:
                    return None
                page = doc[page_idx]
                pix = page.get_pixmap(dpi=300)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=90)
                return buf.getvalue()
        finally:
            doc.close()

    def _load_image(self, fpath):
        img = Image.open(fpath)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=95)
        return buf.getvalue()

    def _ocr_done(self, vin):
        """OCR成功完成（在主线程调用）"""
        self._reset_ui()

        # 填入文本框（必须在主线程）
        self.vin_var.set(vin)

        # 显示结果
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, '✅ VIN识别成功\n\n', 'pass')
        self.result_text.insert(tk.END, f'  {vin}\n', 'vin_display')

        # 自动校验
        self._validate()

    def _validate(self):
        vin = self.vin_var.get().strip()
        if not vin:
            messagebox.showwarning('提示', '请先输入或识别VIN码')
            return

        r = validate_vin(vin)

        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, f'VIN码: {r["vin"]}\n\n', 'vin_display')

        if r['valid']:
            self.result_text.insert(tk.END, '✅ 有效\n', 'pass')
        else:
            self.result_text.insert(tk.END, '❌ 无效\n', 'fail')

if __name__ == '__main__':
    root = tk.Tk()
    app = VinToolApp(root)
    root.mainloop()

"""Render actual Windows screens on a temporary fictional database for review."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import tkinter as tk
import time
from engineer_cabinet.core import Cabinet, MANAGERS
from engineer_cabinet.ui import App
from PIL import Image, ImageGrab
import ctypes
from ctypes import wintypes
ctypes.windll.user32.PrintWindow.argtypes=(wintypes.HWND,wintypes.HDC,wintypes.UINT)
ctypes.windll.user32.PrintWindow.restype=wintypes.BOOL
import win32gui,win32ui,win32con,win32api
mode=win32api.EnumDisplaySettings(None,win32con.ENUM_CURRENT_SETTINGS)
mode.PelsWidth=1600;mode.PelsHeight=1200
print("Display mode change:",win32api.ChangeDisplaySettings(mode,0))

out=Path('ui-review');out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    c=Cabinet(tmp)
    comments=['Согласовать чертёж','Ждём дополнительные данные','Подготовить КП','Монтаж в октябре','Проверить спецификацию','Срочно, приоритет','Готовим документы','Уточнить с поставщиком','Ожидаем чертежи']
    for i in range(9):
        manager=MANAGERS[i%4];q=c.create('quote',manager)
        c.update(q['id'],comment=comments[i],status=('Создан','В работе','Пауза','Готово')[i%4])
        o=c.create('order',manager,124+i,q['id'] if i%3 else None)
        c.update(o['id'],amount=100000+i*15000,comment=comments[i],status=('Создан','В работе','Пауза','Готово')[i%4])
        if i in (4,6,8):c.update(o['id'],month=1)
    root=tk.Tk();app=App(root,c)
    root.geometry('1420x940+0+0')
    def pump():
        deadline=time.monotonic()+10
        stable=None
        while time.monotonic()<deadline:
            root.update();time.sleep(.02)
            if not app.busy and not app.pending_refresh and not app.pending_report:
                if stable is None:stable=time.monotonic()
                if time.monotonic()-stable>=.2:return
            else:stable=None
        raise RuntimeError('UI did not settle')
    pump()
    def capture(window,name):
        hwnd=win32gui.GetParent(window.winfo_id())
        left,top,right,bottom=win32gui.GetWindowRect(hwnd);width=right-left;height=bottom-top
        if window is not root:
            ImageGrab.grab(bbox=(left,top,right,bottom)).save(out/(name+'.png'))
            return
        dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);memory=source.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,width,height);memory.SelectObject(bitmap)
        result=ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)
        if not result:raise RuntimeError('PrintWindow failed')
        image=Image.frombuffer('RGB',(width,height),bitmap.GetBitmapBits(True),'raw','BGRX',0,1)
        image.save(out/(name+'.png'))
        win32gui.DeleteObject(bitmap.GetHandle());memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)
    try:
        for i,name in enumerate(('orders','quotes','report','settings')):
            app.navigate(i)
            if i==2:app.month.set('Январь');app.report()
            pump();capture(root,name)
        app.navigate(0);pump()
        rid=c.rows('order')[0]['id']
        c.create('quote',MANAGERS[0])
        for action,name in ((lambda:app.create_dialog('order'),'create-order'),(lambda:app.card(rid),'card'),
                            (lambda:app.status_dialog(rid),'status-picker'),(lambda:app.delete_dialog(rid),'delete-confirmation')):
            action();pump()
            w=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
            capture(w,name);w.destroy()
    finally:
        app.pool.shutdown(wait=True);c.close()
        for timer in root.tk.call('after','info'):root.after_cancel(timer)
        root.destroy()

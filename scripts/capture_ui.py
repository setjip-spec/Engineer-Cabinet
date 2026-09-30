"""Render actual Windows screens on a temporary fictional database for review."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import tkinter as tk
import time
from engineer_cabinet.core import Cabinet, MANAGERS
from engineer_cabinet.ui import App
from PIL import Image
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
        for _ in range(100):
            root.update();time.sleep(.02)
            if not app.busy and not app.pending_refresh and not app.pending_report:
                root.update();time.sleep(.15);root.update();return
    pump()
    for i,name in enumerate(('orders','quotes','report','settings')):
        app.navigate(i)
        if i==2:app.month.set('Январь');app.report()
        pump()
        hwnd=win32gui.GetParent(root.winfo_id())
        left,top,right,bottom=win32gui.GetWindowRect(hwnd);width=right-left;height=bottom-top
        dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);memory=source.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,width,height);memory.SelectObject(bitmap)
        result=ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)
        if not result:raise RuntimeError('PrintWindow failed')
        image=Image.frombuffer('RGB',(width,height),bitmap.GetBitmapBits(True),'raw','BGRX',0,1)
        image.save(out/(name+'.png'))
        win32gui.DeleteObject(bitmap.GetHandle());memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)
    app.quit()

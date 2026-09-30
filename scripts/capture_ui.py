"""Render actual Windows screens on a temporary fictional database for review."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import tkinter as tk
import time
from engineer_cabinet.core import Cabinet, MANAGERS
from engineer_cabinet.ui import App
from PIL import ImageGrab

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
        ImageGrab.grab().save(out/(name+'.png'))
    app.quit()

"""Version 1.1 acceptance scenarios on disposable files only."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class SearchTests(unittest.TestCase):
    def test_incremental_number_and_russian_comment(self):
        from engineer_cabinet.core import Cabinet,MANAGERS
        with tempfile.TemporaryDirectory() as tmp:
            c=Cabinet(tmp)
            try:
                for num,comment in ((123,'Монтаж ЛЕСТНИЦЫ, север'),(145,'Монтаж ограждения'),(923,'Чертёж лестницы')):
                    r=c.create('order',MANAGERS[0],num);c.update(r['id'],comment=comment)
                def nums(**kw):return [r['num'] for r in c.rows('order',**kw)]
                self.assertEqual(nums(suffix='1'),[123,145])
                self.assertEqual(nums(suffix='12'),[123])
                self.assertEqual(nums(suffix='123'),[123])
                self.assertEqual(nums(suffix='23'),[123,923])
                self.assertEqual(nums(suffix='№00123'),[123])
                self.assertEqual(nums(comment='  лестницы  '),[123,923])
                self.assertEqual(nums(suffix='1',comment='ЛЕСТНИЦЫ'),[123])
                self.assertEqual(nums(comment='нет совпадений'),[])
            finally:c.close()


@unittest.skipUnless(os.environ.get('CABINET_UI_TESTS')=='1','requires desktop session')
class Version11WindowsTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from engineer_cabinet.core import Cabinet
        from engineer_cabinet.ui import App
        self.tmp=tempfile.TemporaryDirectory();self.c=Cabinet(self.tmp.name)
        self.root=tk.Tk();self.errors=[]
        self.root.report_callback_exception=lambda typ,value,tb:self.errors.append(str(value))
        self.error_patch=patch('engineer_cabinet.ui.messagebox.showerror',side_effect=lambda *a,**k:self.errors.append(str(a)))
        self.error_patch.start();self.app=App(self.root,self.c);self.pump()

    def tearDown(self):
        try:
            self.app.pool.shutdown(wait=True);self.c.close()
            for timer in self.root.tk.call('after','info'):self.root.after_cancel(timer)
            self.root.destroy();self.tmp.cleanup()
        finally:self.error_patch.stop()
        self.assertEqual(self.errors,[])

    def pump(self):
        until=time.monotonic()+8
        while time.monotonic()<until:
            self.root.update();time.sleep(.03)
            if not self.app.busy and not self.app.pending_refresh and not self.app.pending_report and not self.app.search_job:
                self.root.update();return
        self.fail('UI worker timeout')

    def window(self,title):
        import tkinter as tk
        return next(w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel) and title in w.title())

    def descendants(self,w):
        for c in w.winfo_children():
            yield c;yield from self.descendants(c)

    def button(self,w,text):
        import tkinter as tk
        from tkinter import ttk
        return next(c for c in self.descendants(w) if isinstance(c,(tk.Button,ttk.Button)) and text in c.cget('text'))

    def test_manager_quote_creation_and_centering(self):
        from tkinter import ttk
        from engineer_cabinet.core import MANAGERS
        q1=self.c.create('quote',MANAGERS[0]);q2=self.c.create('quote',MANAGERS[1])
        self.assertEqual(self.root.state(),'zoomed')
        self.app.create_dialog('order');self.pump();w=self.window('Новая заявка')
        combos=[x for x in self.descendants(w) if isinstance(x,ttk.Combobox)]
        manager,quotes=combos
        self.assertEqual(len(quotes['values']),2)
        self.assertIn(MANAGERS[0],quotes['values'][1]);self.assertNotIn(MANAGERS[1],quotes['values'][1])
        self.assertIn(self.c.today().strftime('%d.%m.%Y'),quotes['values'][1])
        quotes.set(quotes['values'][1]);manager.set(MANAGERS[1]);self.root.update()
        self.assertEqual(quotes.get(),'Без просчёта')
        self.assertEqual(len(quotes['values']),2);self.assertIn(MANAGERS[1],quotes['values'][1])
        # Dialog lies over the application, not at the default screen corner.
        parent_center=self.root.winfo_rootx()+self.root.winfo_width()/2
        child_center=w.winfo_rootx()+w.winfo_width()/2
        self.assertLess(abs(parent_center-child_center),45)
        number=next(x for x in self.descendants(w) if isinstance(x,ttk.Entry) and not isinstance(x,ttk.Combobox))
        number.insert(0,'753');quotes.set(quotes['values'][1]);self.button(w,'Создать').invoke();self.pump()
        order=next(r for r in self.c.rows('order') if r['num']==753)
        self.assertEqual(order['manager'],MANAGERS[1]);self.assertEqual(order['quote_id'],q2['id'])
        self.assertIsNone(self.c.rows('quote',manager=MANAGERS[0])[0]['order_num'])

    def test_status_ready_bonus_and_live_filters(self):
        from engineer_cabinet.core import MANAGERS
        a=self.c.create('order',MANAGERS[0],123);b=self.c.create('order',MANAGERS[0],923)
        self.c.update(a['id'],amount=100000,comment='Лестница север');self.c.update(b['id'],amount=50000,comment='Ограждение')
        self.app.refresh();self.pump();self.assertEqual(self.app.stats[0][0].get(),'4 500 ₽')
        self.app.status_dialog(a['id']);self.pump();w=self.window('Статус —')
        self.button(w,'Готово').invoke();self.pump()
        self.assertEqual(self.c.get(a['id'])['status'],'Готово')
        self.assertEqual(self.app.stats[0][0].get(),'1 500 ₽')
        self.assertEqual(self.app.ready_bonuses[0].get(),'3 000 ₽')
        self.assertEqual(self.app.ready_bonuses[1].get(),'3 000 ₽')
        for query,expected in [('1',1),('12',1),('123',1),('23',2)]:
            self.app.filters['order'][2].set(query);self.pump()
            self.assertEqual(len(self.app.tables['order'].get_children()),expected)
        self.app.filters['order'][5].set('ЛЕСТНИЦА');self.pump()
        self.assertEqual(self.app.tables['order'].get_children(),(a['id'],))
        self.app.reset_filters('order');self.pump()
        self.assertEqual(len(self.app.tables['order'].get_children()),2)

    def test_delete_confirmation_cancel_and_link_preservation(self):
        from engineer_cabinet.core import MANAGERS
        for kind in ('quote','order'):
            q=self.c.create('quote',MANAGERS[0]);o=self.c.create('order',MANAGERS[0],300 if kind=='quote' else 301,q['id'])
            r=q if kind=='quote' else o;partner=o if kind=='quote' else q
            folder=self.c.path(r['folder']);sentinel=folder/'test.txt';sentinel.write_text('test')
            self.app.refresh();self.pump();self.app.card(r['id']);self.pump();card=self.window('Карточка')
            delete_button=self.button(card,'Удалить запись и папку')
            from engineer_cabinet.dialogs import work_area
            self.assertLess(delete_button.winfo_rooty()+delete_button.winfo_height(),work_area(self.root)[3])
            delete_button.invoke();self.pump();confirm=self.window('Подтвердите удаление')
            text=''.join(x.get('1.0','end') for x in self.descendants(confirm) if x.winfo_class()=='Text')
            self.assertIn(str(folder),text);self.assertIn(str(self.c.path(partner['folder'])),text)
            self.button(confirm,'Отмена').invoke();self.pump()
            self.assertTrue(sentinel.exists());self.assertEqual(self.c.get(r['id'])['id'],r['id'])
            self.app.delete_dialog(r['id']);self.pump();confirm=self.window('Подтвердите удаление')
            self.button(confirm,'Удалить запись и файлы').invoke();self.pump()
            self.assertFalse(folder.exists());self.assertTrue(self.c.path(partner['folder']).exists())
            self.assertEqual(self.c.get(partner['id'])['id'],partner['id'])
            if kind=='quote':self.assertIsNone(self.c.get(o['id'])['quote_id'])
        self.c.update(o['id'] if kind=='quote' else self.c.rows('order')[0]['id'],amount=0,month=1)
        closed=next(r for r in self.c.rows('order') if r['status']=='Завершённая')
        menu=self.app.record_menu('order',closed)
        self.assertEqual(menu.entrycget(menu.index('end'),'state'),'disabled');menu.destroy()
        with patch('engineer_cabinet.ui.messagebox.showinfo') as info:
            self.app.delete_dialog(closed['id']);self.pump();info.assert_called_once()
        self.assertEqual(self.c.get(closed['id'])['status'],'Завершённая')

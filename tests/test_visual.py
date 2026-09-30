"""Behavior checks for the new grid; same domain API as the accepted prototype."""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

@unittest.skipUnless(os.environ.get('CABINET_UI_TESTS')=='1','requires desktop session')
class VisualTests(unittest.TestCase):
    def test_grid_actions_filters_and_month_names(self):
        import tkinter as tk
        from engineer_cabinet.core import Cabinet,MANAGERS
        from engineer_cabinet.ui import App
        from engineer_cabinet.widgets import MONTHS
        with tempfile.TemporaryDirectory() as tmp:
            c=Cabinet(tmp);q=c.create('quote',MANAGERS[0]);o=c.create('order',MANAGERS[0],123,q['id'])
            c.update(o['id'],amount=100000,status='В работе')
            root=tk.Tk();errors=[]
            root.report_callback_exception=lambda typ,value,tb:errors.append(str(value))
            with patch('engineer_cabinet.ui.messagebox.showerror',side_effect=lambda *a,**k:errors.append(str(a))):
                app=App(root,c)
                def pump():
                    deadline=time.monotonic()+8
                    while time.monotonic()<deadline:
                        root.update();time.sleep(.02)
                        if not app.busy and not app.pending_refresh and not app.pending_report:
                            root.update();return
                    self.fail('UI timeout')
                try:
                    pump();table=app.tables['order'];row=app.cache['order']['rows'][o['id']]
                    self.assertEqual(app.stats[0][0].get(),'3 000 ₽')
                    self.assertEqual(app.stats[1][0].get(),'3 000 ₽')
                    # Click a comment: an editor appears, no card. Enter persists it.
                    x=sum(size for _,_,size in table.columns[:5])+25
                    table.click(SimpleNamespace(x=x,y=24));root.update()
                    self.assertIsNotNone(table.editor)
                    self.assertFalse(any(isinstance(w,tk.Toplevel) for w in root.winfo_children()))
                    table.editor.insert(0,'Комментарий из таблицы');table.editor.focus_force();root.update()
                    table.editor.event_generate('<Return>');pump()
                    self.assertEqual(c.get(o['id'])['comment'],'Комментарий из таблицы')
                    # Each folder action has a separate hit target and never opens a card.
                    with patch('engineer_cabinet.ui.open_folder') as folder,patch.object(app,'card') as card:
                        for action in ('folder','related_folder'):
                            hit=next(h for h in table.hits if h[5]==action)
                            table.click(SimpleNamespace(x=(hit[0]+hit[2])/2,y=(hit[1]+hit[3])/2));pump()
                        self.assertEqual(folder.call_count,2);card.assert_not_called()
                    app.filters['order'][4].set('Пауза');app.refresh();pump()
                    self.assertEqual(table.get_children(),())
                    app.reset_filters('order');pump();self.assertEqual(len(table.get_children()),1)
                    app.navigate(1);pump();self.assertEqual(app.tabs.index('current'),1)
                    self.assertEqual(app.stats[1][1]['В работе'].get(),'1')
                    c.update(o['id'],month=1);app.navigate(2);app.month.set('Январь');app.report();pump()
                    self.assertIn('Январь',app.total.get());self.assertIn('3 000',app.total.get())
                    self.assertIn('Январь '+str(c.today().year),app.period_list['values'])
                    app.card(o['id']);pump()
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child;yield from descendants(child)
                    from tkinter import ttk
                    combos=[w for w in descendants(root) if isinstance(w,ttk.Combobox)]
                    self.assertTrue(any(tuple(w['values'])==MONTHS and w.get()=='Январь' for w in combos))
                    for w in root.winfo_children():
                        if isinstance(w,tk.Toplevel):w.destroy()
                    self.assertEqual(errors,[])
                finally:
                    app.pool.shutdown(wait=True);c.close()
                    for timer in root.tk.call('after','info'):root.after_cancel(timer)
                    root.destroy()

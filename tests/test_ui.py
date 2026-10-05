import os
import tempfile
import time
import unittest
from unittest.mock import patch

@unittest.skipUnless(os.environ.get('CABINET_UI_TESTS')=='1','requires graphical Windows session')
class WindowTests(unittest.TestCase):
    def test_forms_and_monthly_report(self):
        import tkinter as tk
        from engineer_cabinet.core import Cabinet,MANAGERS
        from engineer_cabinet.ui import App
        with tempfile.TemporaryDirectory() as tmp:
            c=Cabinet(tmp);q=c.create('quote',MANAGERS[0]);o=c.create('order',MANAGERS[0],123,q['id'])
            active=c.create('order',MANAGERS[0],124);c.update(active['id'],status='В работе')
            c.update(o['id'],amount=100000,month=9)
            root=tk.Tk();errors=[]
            root.report_callback_exception=lambda typ,value,tb:errors.append(str(value))
            with patch('engineer_cabinet.ui.messagebox.showerror',side_effect=lambda *a,**kw:errors.append(str(a))):
                app=App(root,c)
                def pump():
                    until=time.monotonic()+8
                    while time.monotonic()<until:
                        root.update();time.sleep(.03)
                        if not app.busy:return
                    self.fail('UI worker timed out')
                pump()
                self.assertEqual(len(app.tables['quote'].get_children()),1)
                status_vars=app.status_filters['order']['vars']
                for v in status_vars.values():v.set(False)
                status_vars['В работе'].set(True);status_vars['Завершённая'].set(True)
                app.status_filter_changed('order');pump()
                self.assertEqual(len(app.tables['order'].get_children()),2)
                app.filters['order'][0].set('Активные + готовые');app.changed_filter();pump()
                self.assertEqual(app.tables['order'].get_children(),(active['id'],))
                app.save_filter_state()
                saved=c.preference('filters_v1')
                self.assertEqual(saved['order']['group'],'Активные + готовые')
                self.assertEqual(saved['order']['statuses'],['В работе','Завершённая'])
                app.card(o['id']);pump()
                self.assertTrue(any(isinstance(w,tk.Toplevel) for w in root.winfo_children()))
                for w in root.winfo_children():
                    if isinstance(w,tk.Toplevel):w.destroy()
                app.create_dialog('order');pump()
                for w in root.winfo_children():
                    if isinstance(w,tk.Toplevel):w.destroy()
                app.year.set(str(c.today().year));app.month.set('9');app.report();pump()
                self.assertIn('3 000',app.total.get())
                app.show_history(o['id']);pump()
                self.assertEqual(errors,[])
                app.quit()

if __name__=='__main__':unittest.main()

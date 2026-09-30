import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from engineer_cabinet.core import Cabinet,CabinetError,MANAGERS,CLOSED,money,earnings,number,restore_backup

class CabinetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'Кабинет'
        self.c=Cabinet(self.root,today=lambda:date(2026,9,30))
    def tearDown(self):self.c.close();self.tmp.cleanup()
    def order(self,n=123,**kw):return self.c.create('order',MANAGERS[0],n,**kw)
    def quote(self,m=MANAGERS[0]):return self.c.create('quote',m)
    def test_number(self):
        o=self.order('00123');self.assertEqual(Path(o['folder']).name,'№00123')
        with self.assertRaises(CabinetError):self.order(123)
        for bad in ['0','100000','-1','abc','000123']:
            with self.assertRaises(CabinetError):number(bad)
        self.assertEqual(number('99999'),99999)
    def test_counter(self):
        a=self.quote();b=self.quote();c=self.quote();self.assertEqual(self.quote(MANAGERS[1])['num'],1)
        self.c.delete(a['id'],True);self.c.delete(c['id'],True)
        self.assertEqual(self.quote()['num'],1);self.assertEqual(self.quote()['num'],3)
        self.assertEqual(self.c.get(b['id'])['num'],2)
        self.assertTrue((self.root/'Просчеты'/MANAGERS[0]/'№00001_30.09.26'/'Чертежи').is_dir())
    def test_money(self):
        self.assertEqual(money('1016,60'),1017);self.assertEqual(earnings(money('1016,60')),31)
        self.assertEqual(money('0.49'),0);self.assertEqual(money('0.50'),1);self.assertIsNone(money(''))
        for bad in ['-0.01','-1','nan','Infinity','1e20','x']:
            with self.assertRaises(CabinetError):money(bad)
    def test_close_requires_sum_zero_valid(self):
        o=self.order();self.assertIsNone(o['amount'])
        with self.assertRaises(CabinetError):self.c.update(o['id'],month=9)
        self.assertIsNone(self.c.get(o['id'])['close_month'])
        self.c.update(o['id'],amount='0',month=9)
        for a in ['',None,'-1']:
            with self.assertRaises(CabinetError):self.c.update(o['id'],amount=a)
        self.assertEqual(self.c.report(2026,9)[1],0)
    def test_report_history_keeps_year(self):
        o=self.order();self.c.update(o['id'],amount='100000',month=9)
        self.c.today=lambda:date(2027,1,2);self.c.update(o['id'],amount='120000',month=10)
        self.assertEqual(self.c.report(2026,9)[1],0);self.assertEqual(self.c.report(2026,10)[1],3600)
        self.assertEqual(self.c.get(o['id'])['close_year'],2026)
        self.assertEqual(json.loads(self.c.history(o['id'])[0]['before_json'])['amount'],100000)
        for kw in [dict(status='В работе'),dict(month='')]:
            with self.assertRaises(CabinetError):self.c.update(o['id'],**kw)
        with self.assertRaises(CabinetError):self.c.delete(o['id'],True)
    def test_sum_rounded_per_order(self):
        for n in [1,2]:self.c.update(self.order(n)['id'],amount='50',month=9)
        self.assertEqual(self.c.report(2026,9)[1],4)
    def test_links_manager_independent(self):
        q=self.quote(MANAGERS[1]);o=self.order(1,quote_id=q['id']);self.assertEqual(o['manager'],MANAGERS[1])
        with self.assertRaises(CabinetError):self.order(2,quote_id=q['id'])
        self.c.update(o['id'],manager=MANAGERS[2]);self.c.update(o['id'],quote_id=None)
        self.assertEqual(self.c.get(o['id'])['manager'],MANAGERS[2]);self.assertEqual(self.c.get(q['id'])['manager'],MANAGERS[1])
        self.order(2,quote_id=q['id'])
    def test_delete_quote_keeps_order(self):
        q=self.quote();o=self.order(quote_id=q['id']);p=self.c.path(o['folder'])/'x';p.write_text('keep')
        with self.assertRaises(CabinetError):self.c.delete(q['id'])
        self.c.delete(q['id'],True);self.assertIsNone(self.c.get(o['id'])['quote_id'])
        self.assertEqual(p.read_text(),'keep');self.assertFalse(self.c.path(q['folder']).exists())
    def test_delete_order_keeps_quote(self):
        q=self.quote();o=self.order(quote_id=q['id']);self.c.delete(o['id'],True)
        self.assertTrue(self.c.path(q['folder']).exists());self.assertIsNone(self.c.rows('quote')[0]['order_num'])
    def test_existing_folder_preserved(self):
        p=self.root/'Заявки'/'№00123';p.mkdir();(p/'existing.dwg').write_bytes(b'drawing');o=self.order()
        self.assertEqual(o['folder_state'],'ready');self.assertEqual((p/'existing.dwg').read_bytes(),b'drawing');self.assertTrue((p/'Фото').is_dir())
    def test_folder_retry(self):
        p=self.root/'Заявки'/'№00123';p.mkdir();(p/'Чертежи').write_text('conflict');o=self.order()
        self.assertEqual(o['folder_state'],'error');self.assertIn(str(p),o['folder_error'])
        (p/'Чертежи').unlink();self.assertTrue(self.c.retry(o['id']));self.assertEqual(len(self.c.rows('order')),1)
    def test_rename_conflict(self):
        o=self.order();p=self.c.path(o['folder']);(p/'x').write_text('keep');target=p.with_name('№00456');target.mkdir()
        with self.assertRaises(CabinetError):self.c.rename_order(o['id'],456,True)
        self.assertEqual(self.c.get(o['id'])['num'],123);target.rmdir()
        self.assertEqual(self.c.rename_order(o['id'],456,True)['num'],456);self.assertEqual((target/'x').read_text(),'keep')
    def test_pending_rename_reserves_target(self):
        o=self.order()
        with patch('engineer_cabinet.core.Path.rename',side_effect=PermissionError('locked')):
            r=self.c.rename_order(o['id'],456,True)
        self.assertEqual(r['folder_state'],'error')
        with self.assertRaises(CabinetError):self.order(456)
        self.assertTrue(self.c.retry(o['id']))
        self.assertEqual(self.c.get(o['id'])['num'],456)

    def test_single_instance_lock(self):
        with self.assertRaises(CabinetError):Cabinet(self.root)

    def test_partial_delete_reserves_number(self):
        q=self.quote()
        with patch('engineer_cabinet.core.shutil.rmtree',side_effect=PermissionError('locked')):self.assertFalse(self.c.delete(q['id'],True))
        self.assertEqual(self.c.get(q['id'])['folder_state'],'error');self.assertEqual(self.quote()['num'],2)
        self.assertTrue(self.c.retry(q['id']));self.assertEqual(self.quote()['num'],1)
    def test_rebind_protection(self):
        q=self.quote();p=self.c.path(q['folder']);m=p.with_name('moved');p.rename(m);self.c.bind_folder(q['id'],m,True)
        self.assertEqual(self.c.path(self.c.get(q['id'])['folder']),m)
        for bad in [self.root,self.root/'Данные',self.root/'Просчеты'/MANAGERS[0],Path(self.root.anchor)]:
            with self.assertRaises(CabinetError):self.c.bind_folder(q['id'],bad,True)
    def test_nested_folder_rejected(self):
        q=self.quote();o=self.order()
        with self.assertRaises(CabinetError):self.c.bind_folder(o['id'],self.c.path(q['folder'])/'Чертежи',True)
    def test_sort_search(self):
        self.quote(MANAGERS[1]);q=self.quote(MANAGERS[0]);self.quote(MANAGERS[0]);rows=self.c.rows('quote')
        self.assertEqual([r['num'] for r in rows],[1,1,2]);self.assertEqual([r['manager'] for r in rows[:2]],sorted(MANAGERS[:2]))
        self.c.update(q['id'],status='Готово');self.assertEqual(len(self.c.rows('quote','Готовые')),1)
        self.assertEqual(len(self.c.rows('quote',suffix='00001')),2);self.order(quote_id=q['id'])
        self.assertEqual(len(self.c.rows('quote',link='Без связи')),2)
    def test_reopen(self):
        o=self.order();self.c.update(o['id'],comment='Нужен чертёж',amount='400',month=9)
        self.c.close();self.c=Cabinet(self.root)
        self.assertEqual(self.c.get(o['id'])['comment'],'Нужен чертёж');self.assertEqual(self.c.report(2026,9)[1],12)
    def test_backup_retention_restore(self):
        o=self.order();self.c.update(o['id'],amount=100,month=9);(self.c.path(o['folder'])/'Чертежи'/'test.dwg').write_bytes(b'drawing')
        start=datetime(2026,9,1);self.assertTrue(self.c.backup(now=start));self.assertIsNone(self.c.backup(now=start+timedelta(days=6)))
        for i in range(1,5):last=self.c.backup(now=start+timedelta(days=7*i))
        self.assertEqual(len(list((self.root/'Резервные копии').glob('*.zip'))),3)
        restored=Path(self.tmp.name)/'restore';restore_backup(last,restored);c=Cabinet(restored)
        try:
            self.assertEqual(c.report(2026,9)[1],3);self.assertEqual((c.path(c.get(o['id'])['folder'])/'Чертежи'/'test.dwg').read_bytes(),b'drawing')
        finally:c.close()
        with self.assertRaises(CabinetError):restore_backup(last,restored)
    def test_backup_external(self):
        o=self.order();ext=Path(self.tmp.name)/'external';ext.mkdir();(ext/'drawing.dwg').write_bytes(b'external');self.c.bind_folder(o['id'],ext,True)
        archive=self.c.backup(True);restored=Path(self.tmp.name)/'restore';restore_backup(archive,restored);c=Cabinet(restored)
        try:self.assertEqual((c.path(c.get(o['id'])['folder'])/'drawing.dwg').read_bytes(),b'external')
        finally:c.close()
    def test_failed_backup_preserves_previous(self):
        self.quote();self.c.backup(True)
        with patch.object(self.c,'_zip_folder',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.c.backup(True)
        self.assertEqual(len(list((self.root/'Резервные копии').glob('*.zip'))),1)
        self.assertFalse(list((self.root/'Резервные копии').glob('*.partial')))

if __name__=='__main__':unittest.main()

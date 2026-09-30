from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

MANAGERS = ('Сергей Головко', 'Валерий Проничкин', 'Калинина Жанна', 'Алексей Трубченинов')
STATUSES = ('Создан', 'В работе', 'Пауза', 'Готово')
CLOSED = 'Завершённая'
MARKER = '.engineer-record.json'
PARTS = {'quote': ('Задание', 'Чертежи'), 'order': ('Задание', 'Чертежи', 'Фото', 'На отправку')}


class CabinetError(ValueError):
    pass


def number(value):
    s = str(value).strip().removeprefix('№').strip()
    if not re.fullmatch(r'[0-9]{1,5}', s) or not 1 <= int(s) <= 99999:
        raise CabinetError('Номер должен быть от 00001 до 99999 (не более пяти цифр).')
    return int(s)


def money(value):
    if value is None or str(value).strip() == '':
        return None
    s = str(value).strip().replace(' ', '').replace('\u00a0', '').replace(',', '.')
    if not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', s):
        raise CabinetError('Введите неотрицательную сумму в рублях.')
    try:
        d = Decimal(s)
        if not d.is_finite() or d < 0 or d > Decimal('999999999999'):
            raise CabinetError('Сумма должна быть от 0 до 999 999 999 999 рублей.')
        return int(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise CabinetError('Некорректная сумма.') from exc


def earnings(amount):
    return None if amount is None else (amount * 3 + 50) // 100


def linked_path(p):
    return p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())


class Cabinet:
    def __init__(self, root, today=date.today):
        self.root = Path(root).absolute()
        self.today = today
        self.lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._no_links(self.root)
        for part in ('Данные', 'Просчеты', 'Заявки', 'Резервные копии'):
            (self.root / part).mkdir(exist_ok=True)
            self._no_links(self.root / part)
        # Only one application instance may mutate this root.
        self._guard = (self.root / 'Данные' / 'session.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self._guard.seek(0)
                self._guard.write(b'0')
                self._guard.flush()
                self._guard.seek(0)
                msvcrt.locking(self._guard.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._guard.close()
            raise CabinetError('Эта база уже открыта другим экземпляром программы.') from exc
        self.db_path = self.root / 'Данные' / 'cabinet.sqlite3'
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS records(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('quote','order')),
            num INTEGER NOT NULL CHECK(num BETWEEN 1 AND 99999), manager TEXT NOT NULL,
            created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Создан', comment TEXT NOT NULL DEFAULT '',
            amount INTEGER CHECK(amount>=0), close_year INTEGER, close_month INTEGER,
            quote_id TEXT UNIQUE REFERENCES records(id) ON DELETE SET NULL,
            folder TEXT NOT NULL, folder_state TEXT NOT NULL DEFAULT 'pending', folder_error TEXT NOT NULL DEFAULT '',
            operation TEXT,
            CHECK(kind='order' OR (amount IS NULL AND close_year IS NULL AND close_month IS NULL)),
            CHECK((close_year IS NULL AND close_month IS NULL AND status!='Завершённая') OR
              (kind='order' AND close_year IS NOT NULL AND close_month BETWEEN 1 AND 12 AND status='Завершённая' AND amount IS NOT NULL))
          );
          CREATE UNIQUE INDEX IF NOT EXISTS quote_number ON records(manager,num) WHERE kind='quote';
          CREATE UNIQUE INDEX IF NOT EXISTS order_number ON records(num) WHERE kind='order';
          CREATE INDEX IF NOT EXISTS financial_period ON records(close_year,close_month);
          CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, at TEXT NOT NULL,
            event TEXT NOT NULL, before_json TEXT, after_json TEXT);
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")
        if self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != '1':
            self.close()
            raise CabinetError('Версия базы не поддерживается. Не открывайте её старой программой.')

    def close(self):
        with self.lock:
            self.db.close()
            self._guard.close()

    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def _no_links(self, p):
        for item in (p, *p.parents):
            if linked_path(item):
                raise CabinetError(f'Символические ссылки и junction не поддерживаются: {item}')

    def path(self, stored):
        p = Path(stored)
        return p if p.is_absolute() else self.root / p

    def _stored(self, path):
        p = Path(path).absolute()
        return str(p.relative_to(self.root)) if p.is_relative_to(self.root) else str(p)

    def _event(self, rid, action, before=None, after=None):
        self.db.execute('INSERT INTO history(record_id,at,event,before_json,after_json) VALUES(?,?,?,?,?)',
                        (rid, datetime.now().isoformat(timespec='seconds'), action,
                         json.dumps(before, ensure_ascii=False) if before else None,
                         json.dumps(after, ensure_ascii=False) if after else None))

    def get(self, rid):
        with self.lock:
            r = self.db.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
            if not r:
                raise CabinetError('Запись не найдена.')
            return dict(r)

    def _editable(self, rid):
        r = self.get(rid)
        if r['operation']:
            raise CabinetError('Сначала завершите файловую операцию кнопкой «Повторить».')
        return r

    def _manager(self, name):
        if name not in MANAGERS:
            raise CabinetError('Выберите менеджера из списка.')

    def rows(self, kind, group='Все', manager='', suffix='', link='Все'):
        with self.lock:
            result = []
            for row in self.db.execute('''SELECT r.*, q.num AS quote_num, q.folder AS quote_folder,
                o.num AS order_num FROM records r
                LEFT JOIN records q ON r.quote_id=q.id
                LEFT JOIN records o ON o.quote_id=r.id WHERE r.kind=? ORDER BY r.num,r.manager COLLATE BINARY''', (kind,)):
                r = dict(row)
                if manager and r['manager'] != manager:
                    continue
                if suffix and not f"{r['num']:05d}".endswith(suffix.strip().removeprefix('№')):
                    continue
                if group == 'Активные' and r['status'] not in STATUSES[:3]:
                    continue
                if group == 'Готовые' and r['status'] != 'Готово':
                    continue
                if group == 'Завершённые' and r['status'] != CLOSED:
                    continue
                related = r['order_num'] if kind == 'quote' else r['quote_num']
                if link == 'Без связи' and related is not None:
                    continue
                if link == 'Со связью' and related is None:
                    continue
                r['earnings'] = earnings(r['amount'])
                result.append(r)
            return result

    def _folder_candidate(self, r):
        if r['kind'] == 'quote':
            stamp = date.fromisoformat(r['created']).strftime('%d.%m.%y')
            return self.root / 'Просчеты' / r['manager'] / f"№{r['num']:05d}_{stamp}"
        return self.root / 'Заявки' / f"№{r['num']:05d}"

    def _check_folder(self, p, rid):
        p = Path(p).absolute()
        self._no_links(p)
        protected = [self.root, self.root / 'Данные', self.root / 'Резервные копии',
                     self.root / 'Просчеты', self.root / 'Заявки', *(self.root / 'Просчеты' / m for m in MANAGERS)]
        if p == Path(p.anchor) or any(x == p or x.is_relative_to(p) for x in protected):
            raise CabinetError(f'Нельзя привязать или удалить общую папку: {p}')
        if p.is_relative_to(self.root / 'Данные') or p.is_relative_to(self.root / 'Резервные копии'):
            raise CabinetError(f'Служебная папка: {p}')
        for r in self.db.execute('SELECT id,folder FROM records WHERE id!=?', (rid,)):
            other = self.path(r['folder'])
            if other == p or other.is_relative_to(p) or p.is_relative_to(other):
                raise CabinetError(f'Папка пересекается с другой записью: {p}\nДругая привязка: {other}')
        marker = p / MARKER
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8'))['record_id'] != rid:
                raise CabinetError(f'Папка принадлежит другой записи: {p}')
        return p

    def _claim(self, p, rid):
        marker = p / MARKER
        if not marker.exists():
            with marker.open('x', encoding='utf-8') as f:
                json.dump({'record_id': rid}, f)

    def _prepare(self, r, p):
        p = self._check_folder(p, r['id'])
        p.mkdir(parents=True, exist_ok=True)
        self._claim(p, r['id'])
        for name in PARTS[r['kind']]:
            child = p / name
            self._no_links(child)
            child.mkdir(exist_ok=True)
        return p

    def _order_number_busy(self, num, exclude=None):
        for r in self.db.execute("SELECT id,num,operation FROM records WHERE kind='order'"):
            if r['id']==exclude:
                continue
            if r['num']==num:
                return True
            if r['operation']:
                op=json.loads(r['operation'])
                if op.get('type')=='rename' and op.get('number')==num:
                    return True
        return False

    def create(self, kind, manager, num=None, quote_id=None):
        if kind not in PARTS:
            raise CabinetError('Неизвестный тип записи.')
        with self.tx():
            if quote_id:
                if kind != 'order':
                    raise CabinetError('Связь задаётся только в заявке.')
                q = self._free_quote(quote_id)
                manager = q['manager']
            self._manager(manager)
            if kind == 'quote':
                used = {r[0] for r in self.db.execute("SELECT num FROM records WHERE kind='quote' AND manager=?", (manager,))}
                num = next((n for n in range(1, 100000) if n not in used), None)
                if num is None:
                    raise CabinetError('Все пятизначные номера этого менеджера заняты.')
            else:
                num = number(num)
                if self._order_number_busy(num):
                    raise CabinetError(f'Заявка №{num:05d} уже существует, включая завершённые.')
            r = dict(id=uuid.uuid4().hex, kind=kind, num=num, manager=manager, created=self.today().isoformat())
            p = self._folder_candidate(r)
            self.db.execute('INSERT INTO records(id,kind,num,manager,created,folder,quote_id) VALUES(?,?,?,?,?,?,?)',
                            (r['id'],kind,num,manager,r['created'],self._stored(p),quote_id))
            self._event(r['id'],'Создание',after=self.get(r['id']))
        self.retry(r['id'])
        return self.get(r['id'])

    def retry(self, rid):
        with self.lock:
            r = self.get(rid)
            try:
                if r['operation']:
                    self._resume(r)
                else:
                    self._prepare(r, self.path(r['folder']))
                    with self.db:
                        self.db.execute("UPDATE records SET folder_state='ready',folder_error='' WHERE id=?", (rid,))
            except (OSError, ValueError, sqlite3.Error) as exc:
                with self.db:
                    self.db.execute("UPDATE records SET folder_state='error',folder_error=? WHERE id=?",
                                    (f"{exc}\nПуть: {self.path(r['folder'])}", rid))
                return False
            return True

    def _free_quote(self, qid, order_id=None):
        q = self._editable(qid)
        if q['kind'] != 'quote':
            raise CabinetError('Выберите просчёт.')
        other = self.db.execute('SELECT id,num FROM records WHERE quote_id=?', (qid,)).fetchone()
        if other and other['id'] != order_id:
            raise CabinetError(f"Просчёт уже связан с заявкой №{other['num']:05d}.")
        return q

    def update(self, rid, *, comment=None, status=None, amount=..., month=..., manager=None, quote_id=...):
        with self.tx():
            old = self._editable(rid)
            new = dict(old)
            if comment is not None:
                new['comment'] = comment
            if status is not None:
                if old['status'] == CLOSED and status != CLOSED:
                    raise CabinetError('Завершённую заявку нельзя вернуть в работу.')
                if status not in STATUSES and status != old['status']:
                    raise CabinetError('Для завершения укажите месяц закрытия.')
                new['status'] = status
            if quote_id is not ...:
                if old['kind'] != 'order':
                    raise CabinetError('Связь редактируется в заявке.')
                if quote_id:
                    q = self._free_quote(quote_id, rid)
                    if quote_id != old['quote_id']:
                        new['manager'] = q['manager']
                new['quote_id'] = quote_id or None
            if manager is not None:
                self._manager(manager)
                if old['kind'] == 'quote' and manager != old['manager']:
                    raise CabinetError('Менеджер просчёта закреплён.')
                new['manager'] = manager
            if amount is not ...:
                if old['kind'] != 'order':
                    raise CabinetError('Финансы только в заявках.')
                new['amount'] = money(amount)
            if month is not ...:
                if old['kind'] != 'order':
                    raise CabinetError('Месяц закрытия только у заявок.')
                if month in ('',None):
                    if old['close_month'] is not None:
                        raise CabinetError('Нельзя снять закрытие заявки.')
                else:
                    if not str(month).isdigit() or not 1 <= int(month) <= 12:
                        raise CabinetError('Месяц должен быть от 1 до 12.')
                    if new['amount'] is None:
                        raise CabinetError('Перед закрытием заполните сумму. Ноль допустим.')
                    new.update(close_month=int(month),close_year=old['close_year'] or self.today().year,status=CLOSED)
            if new['status'] == CLOSED and new['amount'] is None:
                raise CabinetError('Нельзя стереть сумму завершённой заявки. Можно заменить на 0.')
            cols=('comment','status','amount','close_month','close_year','manager','quote_id')
            self.db.execute('UPDATE records SET '+','.join(k+'=?' for k in cols)+' WHERE id=?',
                            (*[new[k] for k in cols],rid))
            if old != new:
                self._event(rid,'Изменение',old,new)
        return self.get(rid)

    def bind_folder(self, rid, folder, confirmed=False):
        if not confirmed:
            raise CabinetError('Подтвердите привязку папки: при удалении записи удаляется её содержимое.')
        with self.lock:
            r = self._editable(rid)
            p = Path(folder).absolute()
            if not p.is_dir():
                raise CabinetError(f'Папка не найдена: {p}')
            self._prepare(r,p)
            with self.tx():
                self.db.execute("UPDATE records SET folder=?,folder_state='ready',folder_error='' WHERE id=?", (self._stored(p),rid))
                self._event(rid,'Привязка папки',r,self.get(rid))
            # An old directory remains untouched; marker intentionally prevents accidental reuse.
        return self.get(rid)

    def details(self, rid):
        with self.lock:
            r = self.get(rid)
            lines=[f"{'Просчёт' if r['kind']=='quote' else 'Заявка'} №{r['num']:05d}",
                   f"Менеджер: {r['manager']}",f"Собственная папка: {self.path(r['folder'])}"]
            related = self.db.execute('SELECT * FROM records WHERE id=? OR quote_id=?', (r['quote_id'],rid)).fetchall()
            for other in related:
                lines.append(f"Связь: №{other['num']:05d}; {self.path(other['folder'])}")
            if r['operation']:
                lines.append('Операция: '+r['operation'])
            return '\n'.join(lines)

    def rename_order(self, rid, value, confirmed=False):
        if not confirmed:
            raise CabinetError('Изменение номера требует подтверждения.')
        num=number(value)
        with self.tx():
            r=self._editable(rid)
            if r['kind']!='order':
                raise CabinetError('Номер просчёта не редактируется.')
            if num==r['num']:
                return r
            if self._order_number_busy(num,rid):
                raise CabinetError('Номер заявки уже занят.')
            source=self._check_folder(self.path(r['folder']),rid)
            target=self._check_folder(source.with_name(f'№{num:05d}'),rid)
            if target.exists():
                raise CabinetError(f'Переименование невозможно: путь уже существует: {target}\n'+self.details(rid))
            if not source.is_dir() or not (source/MARKER).exists():
                raise CabinetError(f'Не найдена принадлежащая записи папка: {source}')
            op=dict(type='rename',source=self._stored(source),target=self._stored(target),number=num)
            self.db.execute('UPDATE records SET operation=? WHERE id=?',(json.dumps(op),rid))
        self.retry(rid)
        return self.get(rid)

    def delete(self, rid, confirmed=False):
        if not confirmed:
            raise CabinetError('Удаление записи и её файлов требует подтверждения.')
        with self.tx():
            r=self._editable(rid)
            if r['status']==CLOSED:
                raise CabinetError('Завершённую заявку удалять нельзя.')
            p=self._check_folder(self.path(r['folder']),rid)
            if p.exists() and not (p/MARKER).exists():
                raise CabinetError(f'Папка не подтверждена как принадлежащая записи: {p}')
            stage=p.with_name('.cabinet-delete-'+rid)
            if stage.exists():
                raise CabinetError(f'Конфликт пути удаления: {stage}')
            op=dict(type='delete',source=self._stored(p),target=self._stored(stage))
            self.db.execute('UPDATE records SET operation=? WHERE id=?',(json.dumps(op),rid))
        return self.retry(rid)

    def _resume(self, r):
        op=json.loads(r['operation'])
        source=self._check_folder(self.path(op['source']),r['id'])
        target=self._check_folder(self.path(op['target']),r['id'])
        if op['type']=='rename':
            if source.exists():
                if target.exists():
                    raise CabinetError(f'Путь уже существует: {target}')
                source.rename(target)
            if not target.is_dir() or not (target/MARKER).exists():
                raise CabinetError(f'Не удалось подтвердить результат: {target}')
            with self.tx():
                self.db.execute("UPDATE records SET num=?,folder=?,operation=NULL,folder_state='ready',folder_error='' WHERE id=?",
                                (op['number'],op['target'],r['id']))
                self._event(r['id'],'Номер и папка изменены',r,self.get(r['id']))
        elif op['type']=='delete':
            if source.exists():
                if target.exists():
                    raise CabinetError(f'Конфликт удаления: {target}')
                source.rename(target)
            if target.exists():
                # Reject nested symlinks/junctions before traversal or deletion.
                self._walk_files(target)
                shutil.rmtree(target)
            with self.tx():
                self._event(r['id'],'Удаление с папкой',r,None)
                related=self.db.execute('SELECT * FROM records WHERE quote_id=?',(r['id'],)).fetchall()
                for o in related:
                    after=dict(o);after['quote_id']=None
                    self._event(o['id'],'Удалён связанный просчёт',dict(o),after)
                self.db.execute('DELETE FROM records WHERE id=?',(r['id'],))
        else:
            raise CabinetError('Неизвестная незавершённая операция.')

    def report(self, year, month):
        with self.lock:
            rows=[dict(r) for r in self.db.execute("SELECT * FROM records WHERE kind='order' AND close_year=? AND close_month=? ORDER BY num",(int(year),int(month)))]
            for r in rows:
                r['earnings']=earnings(r['amount'])
            return rows,sum(r['earnings'] for r in rows)

    def periods(self):
        with self.lock:
            return [(r[0],r[1]) for r in self.db.execute('SELECT DISTINCT close_year,close_month FROM records WHERE close_year IS NOT NULL ORDER BY close_year DESC,close_month DESC')]

    def history(self, rid=None):
        with self.lock:
            sql='SELECT * FROM history'+(' WHERE record_id=?' if rid else '')+' ORDER BY id DESC'
            return [dict(r) for r in self.db.execute(sql,(rid,) if rid else ())]

    def _walk_files(self, root):
        files=[]
        for here, dirs, names in os.walk(root,followlinks=False):
            for name in dirs+names:
                p=Path(here)/name
                self._no_links(p)
                if p.is_file():
                    files.append(p)
        return files

    def backup(self, force=False, now=None):
        now=now or datetime.now()
        with self.lock:
            last=self.db.execute("SELECT value FROM meta WHERE key='last_backup'").fetchone()
            if not force and last and now-datetime.fromisoformat(last[0])<timedelta(days=7):
                return None
            if self.db.execute("SELECT 1 FROM records WHERE operation IS NOT NULL OR folder_state!='ready'").fetchone():
                raise CabinetError('Резервирование отложено: сначала завершите операции с папками.')
            dest=self.root/'Резервные копии'
            stamp=now.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
            work=dest/('pending-'+stamp)
            work.mkdir()
            temp=dest/(stamp+'.partial')
            final=dest/('cabinet-'+stamp+'.zip')
            try:
                snap=work/'cabinet.sqlite3'
                with sqlite3.connect(snap) as target:
                    self.db.backup(target)
                manifest={'schema':1,'created':now.isoformat(),'root':str(self.root),'external':[]}
                with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
                    z.write(snap,'Данные/cabinet.sqlite3')
                    for sub in ('Просчеты','Заявки'):
                        self._zip_folder(z,self.root/sub,sub)
                    for r in self.db.execute('SELECT id,folder FROM records'):
                        p=self.path(r['folder'])
                        if not p.is_dir():
                            raise CabinetError(f'Не найдена папка для резервной копии: {p}')
                        if not p.is_relative_to(self.root/'Просчеты') and not p.is_relative_to(self.root/'Заявки'):
                            arc='Дополнительные папки/'+r['id']
                            self._zip_folder(z,p,arc)
                            manifest['external'].append({'id':r['id'],'path':str(p),'archive':arc})
                    z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                with zipfile.ZipFile(temp) as z:
                    if z.testzip():
                        raise CabinetError('Ошибка проверки резервной копии.')
                temp.replace(final)
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO meta VALUES('last_backup',?)",(now.isoformat(),))
                old=sorted(dest.glob('cabinet-*.zip'),key=lambda p:p.name)
                for p in old[:-3]:
                    p.unlink()
                return str(final)
            finally:
                temp.unlink(missing_ok=True)
                shutil.rmtree(work,ignore_errors=True)

    def _zip_folder(self,z,p,arc):
        self._no_links(p)
        z.writestr(arc+'/',b'')
        for here,dirs,names in os.walk(p,followlinks=False):
            for name in dirs:
                d=Path(here)/name;self._no_links(d)
                z.writestr(arc+'/'+d.relative_to(p).as_posix()+'/',b'')
            for name in names:
                f=Path(here)/name;self._no_links(f)
                before=f.stat()
                z.write(f,arc+'/'+f.relative_to(p).as_posix())
                after=f.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise CabinetError(f'Файл изменился во время резервирования. Повторите после сохранения: {f}')


def restore_backup(archive, destination):
    """Restore into a new empty folder, never overwrite a working cabinet."""
    destination=Path(destination).absolute()
    if destination.exists() and any(destination.iterdir()):
        raise CabinetError('Для восстановления выберите новую пустую папку.')
    destination.mkdir(parents=True,exist_ok=True)
    for p in (destination,*destination.parents):
        if linked_path(p):raise CabinetError(f'Путь восстановления содержит ссылку: {p}')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if 'manifest.json' not in names or 'Данные/cabinet.sqlite3' not in names:
            raise CabinetError('Это не резервная копия кабинета.')
        for n in names:
            if Path(n).is_absolute() or '..' in Path(n).parts or '\\' in n or ':' in n:
                raise CabinetError('Небезопасный путь в архиве.')
        if z.testzip():raise CabinetError('Архив повреждён.')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('schema')!=1:raise CabinetError('Версия копии не поддерживается.')
        for x in manifest.get('external',[]):
            arc=x['archive']
            if not isinstance(arc,str) or not arc.startswith('Дополнительные папки/') or '..' in Path(arc).parts or '\\' in arc or ':' in arc:
                raise CabinetError('Некорректная привязка в архиве.')
        z.extractall(destination)
    with sqlite3.connect(destination/'Данные'/'cabinet.sqlite3') as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CabinetError('Копия базы повреждена.')
        for x in manifest.get('external',[]):db.execute('UPDATE records SET folder=? WHERE id=?',(x['archive'],x['id']))
    return str(destination)

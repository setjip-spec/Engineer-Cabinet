"""Functional forms for acceptance testing; final visual design is a later stage."""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .core import Cabinet, CabinetError, MANAGERS, STATUSES, CLOSED, earnings


def open_folder(path):
    p=Path(path)
    if not p.is_dir():
        raise CabinetError(f'Папка недоступна: {p}\nИспользуйте «Указать папку» в карточке.')
    if sys.platform=='win32':
        os.startfile(str(p))
    else:
        import subprocess
        subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(p)])


class App:
    def __init__(self, window, cabinet):
        self.w=window;self.c=cabinet
        self.w.title('Личный кабинет инженера — рабочий прототип')
        self.w.geometry('1350x780')
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.results=queue.Queue();self.busy=False;self.tables={};self.filters={};self.cache={}
        self.notice=tk.StringVar(value=str(self.c.root))
        top=ttk.Frame(window,padding=8);top.pack(fill='x')
        ttk.Label(top,text='Личный кабинет инженера',font=('Segoe UI',16)).pack(side='left')
        ttk.Button(top,text='Настройки / папки',command=self.settings).pack(side='right')
        ttk.Label(window,textvariable=self.notice,wraplength=1250).pack(fill='x',padx=8)
        self.tabs=ttk.Notebook(window);self.tabs.pack(fill='both',expand=True,padx=8,pady=8)
        for kind,title in [('order','Заявки'),('quote','Просчёты')]:
            self.build_list(kind,title)
        self.build_report()
        self.w.after(50,self.drain)
        self.w.protocol('WM_DELETE_WINDOW',self.quit)
        self.refresh()
        self.w.after(2500,self.scheduled_backup)

    def run(self, work, done=None, quiet=False):
        if self.busy:
            self.notice.set('Дождитесь окончания текущей операции.')
            return
        self.busy=True;self.notice.set('Выполняется…')
        def worker():
            try:
                self.results.put((True,work(),done,quiet))
            except Exception as e:
                self.results.put((False,e,done,quiet))
        self.pool.submit(worker)

    def drain(self):
        try:
            while True:
                ok,value,done,quiet=self.results.get_nowait()
                self.busy=False
                if ok:
                    self.notice.set('Сохранено / обновлено. '+str(self.c.root))
                    if done: done(value)
                else:
                    self.notice.set(str(value))
                    if not quiet: messagebox.showerror('Операция не выполнена',str(value),parent=self.w)
        except queue.Empty:
            pass
        self.w.after(50,self.drain)

    def build_list(self,kind,title):
        frame=ttk.Frame(self.tabs);self.tabs.add(frame,text=title)
        bar=ttk.Frame(frame,padding=5);bar.pack(fill='x')
        group=tk.StringVar(value='Активные');manager=tk.StringVar();suffix=tk.StringVar();link=tk.StringVar(value='Все')
        self.filters[kind]=(group,manager,suffix,link)
        groups=['Активные','Готовые','Все']+(['Завершённые'] if kind=='order' else [])
        for var,values,width in [(group,groups,15),(manager,['',*sorted(MANAGERS)],23),(link,['Все','Со связью','Без связи'],14)]:
            combo=ttk.Combobox(bar,textvariable=var,values=values,state='readonly',width=width)
            combo.pack(side='left',padx=3);combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        ttk.Label(bar,text='Конец номера:').pack(side='left')
        entry=ttk.Entry(bar,textvariable=suffix,width=9);entry.pack(side='left');entry.bind('<Return>',lambda e:self.refresh())
        ttk.Button(bar,text='Найти',command=self.refresh).pack(side='left',padx=3)
        ttk.Button(bar,text='Создать',command=lambda:self.create_dialog(kind)).pack(side='right')
        tools=ttk.Frame(frame);tools.pack(fill='x')
        for text,cmd in [('Карточка',lambda:self.selected_card(kind)),('Папка записи',lambda:self.selected_folder(kind)),
                         ('Папка просчёта',lambda:self.selected_folder(kind,True)),('Обновить',self.refresh)]:
            ttk.Button(tools,text=text,command=cmd).pack(side='left',padx=3,pady=3)
        ttk.Label(tools,text='Комментарий: двойной щелчок по ячейке').pack(side='right')
        cols=('num','related','created','manager','status','earnings','comment','folder_state')
        tree=ttk.Treeview(frame,columns=cols,show='headings',selectmode='browse')
        labels=('Номер','Просчёт' if kind=='order' else 'Заявка ✓','Создан','Менеджер','Статус','Мой бонус, ₽','Комментарий','Папка')
        for name,label,width in zip(cols,labels,[85,90,95,185,115,95,310,80]):
            tree.heading(name,text=label);tree.column(name,width=width,stretch=name=='comment')
        if kind=='quote':
            tree['displaycolumns']=tuple(c for c in cols if c!='earnings')
        for status,color in [('Создан','#ffffff'),('В работе','#dbeafe'),('Пауза','#fed7aa'),('Готово','#dcfce7'),(CLOSED,'#86b994')]:
            tree.tag_configure(status,background=color)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview);scroll.pack(side='right',fill='y')
        tree.configure(yscrollcommand=scroll.set);tree.pack(fill='both',expand=True)
        tree.bind('<Double-1>',lambda e:self.cell_double(kind,e))
        tree.bind('<ButtonRelease-1>',lambda e:self.row_click(kind,e))
        self.tables[kind]=tree
        count=ttk.Label(frame,text='');count.pack(anchor='w',padx=5);self.cache[kind]={'count':count,'rows':{}}

    def refresh(self):
        args={k:tuple(v.get() for v in self.filters[k]) for k in self.tables}
        def work():
            return {k:self.c.rows(k,*values) for k,values in args.items()}
        def done(data):
            for kind,rows in data.items():
                tree=self.tables[kind];selected=tree.selection();y=tree.yview()[0]
                tree.delete(*tree.get_children());self.cache[kind]['rows']={r['id']:r for r in rows}
                for r in rows:
                    rel=r['quote_num'] if kind=='order' else r['order_num']
                    tree.insert('','end',iid=r['id'],values=(f"№{r['num']:05d}",f'№{rel:05d}' if rel is not None else '',r['created'],r['manager'],r['status'],
                        '' if r['earnings'] is None else f"{r['earnings']:,}".replace(',',' '),r['comment'],
                        {'ready':'Готова','pending':'Подготовка','error':'Ошибка'}.get(r['folder_state'],r['folder_state'])),tags=(r['status'],))
                if selected and tree.exists(selected[0]):tree.selection_set(selected[0])
                tree.yview_moveto(y)
                self.cache[kind]['count'].configure(text=f'Показано: {len(rows)}; без связи: {sum((r["quote_num"] if kind=="order" else r["order_num"]) is None for r in rows)}')
        self.run(work,done)

    def selected(self,kind):
        selected=self.tables[kind].selection()
        return selected[0] if selected else None

    def selected_card(self,kind):
        rid=self.selected(kind)
        if rid:self.card(rid)

    def selected_folder(self,kind,quote=False):
        rid=self.selected(kind)
        if not rid:return
        r=self.cache[kind]['rows'][rid]
        path=r['quote_folder'] if quote and kind=='order' else r['folder']
        if not path:
            messagebox.showinfo('Папка','У заявки нет связанного просчёта.');return
        self.run(lambda:open_folder(self.c.path(path)))

    def row_click(self,kind,event):
        tree=self.tables[kind];row=tree.identify_row(event.y);col=tree.identify_column(event.x)
        if not row:return
        visible=tuple(tree['displaycolumns'])
        if visible==('#all',):visible=tuple(tree['columns'])
        i=int(col[1:])-1 if col.startswith('#') else -1
        if i>=0 and visible[i] not in ('comment',):
            self.card(row)

    def cell_double(self,kind,event):
        tree=self.tables[kind];rid=tree.identify_row(event.y);col=tree.identify_column(event.x)
        if not rid:return
        visible=tuple(tree['displaycolumns'])
        if visible==('#all',):visible=tuple(tree['columns'])
        i=int(col[1:])-1 if col.startswith('#') else -1
        if i<0 or visible[i]!='comment':return
        bbox=tree.bbox(rid,col)
        if not bbox:return
        x,y,w,h=bbox;entry=ttk.Entry(tree);entry.insert(0,self.cache[kind]['rows'][rid]['comment'])
        entry.place(x=x,y=y,width=w,height=h);entry.focus_set()
        def save(e=None):
            if self.busy:return
            value=entry.get()
            def done(_):entry.destroy();self.refresh()
            self.run(lambda:self.c.update(rid,comment=value),done)
        entry.bind('<Return>',save);entry.bind('<Escape>',lambda e:entry.destroy())

    def create_dialog(self,kind,quotes=None):
        if self.busy:return
        if quotes is None:
            self.run(lambda:self.c.rows('quote'),lambda rows:self.create_dialog(kind,rows))
            return
        w=tk.Toplevel(self.w);w.title('Новая заявка' if kind=='order' else 'Новый просчёт');w.transient(self.w)
        manager=tk.StringVar(value=MANAGERS[0]);num=tk.StringVar();quote=tk.StringVar(value='Без просчёта')
        self.field(w,'Менеджер',manager,MANAGERS)
        if kind=='order':self.field(w,'Номер заявки',num)
        choices={'Без просчёта':None}
        if kind=='order':
            for r in quotes:
                if r['order_num'] is None:choices[f"№{r['num']:05d} — {r['manager']}"]=r['id']
            self.field(w,'Просчёт (можно связать позже в карточке)',quote,list(choices))
        ttk.Label(w,text='Сумма при создании пустая. Папки создаются автоматически.\nВсе просчёты доступны для связи в карточке.').pack(padx=10,pady=8)
        def create():
            if self.busy:return
            # Capture Tk values on the UI thread.
            m,n,q=manager.get(),num.get(),choices[quote.get()]
            def done(r):
                w.destroy();self.refresh()
                if r['folder_state']=='error':messagebox.showwarning('Запись сохранена, папка не готова',r['folder_error']+'\nОткройте карточку → Повторить.')
            self.run(lambda:self.c.create(kind,m,n,q),done)
        ttk.Button(w,text='Создать',command=create).pack(pady=8)

    def field(self,w,label,var,choices=None):
        f=ttk.Frame(w,padding=4);f.pack(fill='x')
        ttk.Label(f,text=label,width=32).pack(side='left')
        widget=ttk.Combobox(f,textvariable=var,values=choices,state='readonly',width=40) if choices is not None else ttk.Entry(f,textvariable=var,width=43)
        widget.pack(side='right',fill='x',expand=True)
        return widget

    def card(self,rid):
        if self.busy:return
        def work():
            return self.c.get(rid),self.c.rows('quote'),self.c.details(rid)
        def done(data):
            r,quotes,details=data
            w=tk.Toplevel(self.w);w.title(f"Карточка №{r['num']:05d}");w.geometry('850x730');w.transient(self.w)
            vars={k:tk.StringVar(value='' if r[k] is None else str(r[k])) for k in ['manager','status','comment','amount','close_month']}
            ttk.Label(w,text=f"Создана: {r['created']}    Год закрытия: {r['close_year'] or '—'}").pack(pady=4)
            man=self.field(w,'Менеджер',vars['manager'],MANAGERS)
            if r['kind']=='quote':man.configure(state='disabled')
            self.field(w,'Статус',vars['status'],[CLOSED] if r['status']==CLOSED else STATUSES)
            self.field(w,'Комментарий',vars['comment'])
            choices={'Без просчёта':None};selected='Без просчёта'
            qvar=tk.StringVar(value=selected)
            if r['kind']=='order':
                for q in quotes:
                    if q['order_num'] is None or q['id']==r['quote_id']:
                        label=f"№{q['num']:05d} — {q['manager']}";choices[label]=q['id']
                        if q['id']==r['quote_id']:selected=label
                qvar.set(selected);combo=self.field(w,'Связанный просчёт',qvar,list(choices))
                def picked(e):
                    qid=choices[qvar.get()]
                    if qid:
                        vars['manager'].set(next(q['manager'] for q in quotes if q['id']==qid))
                        note.set('Менеджер подставлен из просчёта. Можно изменить вручную.')
                combo.bind('<<ComboboxSelected>>',picked)
                note=tk.StringVar();ttk.Label(w,textvariable=note,foreground='#174ea6').pack()
                self.field(w,'Общая сумма, ₽ (для расчёта)',vars['amount'])
                self.field(w,'Месяц закрытия',vars['close_month'],['',*map(str,range(1,13))] if r['status']!=CLOSED else list(map(str,range(1,13))))
                ttk.Label(w,text=f"Мой бонус: {earnings(r['amount']) if r['amount'] is not None else '—'} ₽; ставка 3%").pack()
            pathbox=tk.Text(w,height=7,wrap='word');pathbox.pack(fill='x',padx=8,pady=5);pathbox.insert('1.0',details+'\n'+r['folder_error']);pathbox.configure(state='disabled')
            def saved(_):
                w.destroy()
                if self.tabs.index('current')==2:self.report()
                else:self.refresh()
            def save():
                if self.busy:return
                fields={k:v.get() for k,v in vars.items() if r['kind']=='order' or k in ('status','comment')}
                if r['kind']=='order':fields['month']=fields.pop('close_month');fields['quote_id']=choices[qvar.get()]
                self.run(lambda:self.c.update(rid,**fields),saved)
            ttk.Button(w,text='Сохранить',command=save).pack(pady=5)
            bar=ttk.Frame(w);bar.pack(fill='x',padx=5)
            ttk.Button(bar,text='Папка записи',command=lambda:self.run(lambda:open_folder(self.c.path(r['folder'])))).pack(side='left')
            if r['quote_id']:
                q=next((q for q in quotes if q['id']==r['quote_id']),None)
                if q:ttk.Button(bar,text='Папка просчёта',command=lambda:self.run(lambda:open_folder(self.c.path(q['folder'])))).pack(side='left')
            ttk.Button(bar,text='Повторить',command=lambda:self.run(lambda:self.c.retry(rid),lambda _: (w.destroy(),self.card(rid)))).pack(side='left')
            def bind():
                path=filedialog.askdirectory(parent=w,title='Указать собственную папку записи')
                if path and messagebox.askyesno('Привязка',f'{details}\n\nНовая собственная папка: {path}\nПри удалении записи удаляется всё её содержимое. Привязать?',parent=w):
                    self.run(lambda:self.c.bind_folder(rid,path,True),saved)
            ttk.Button(bar,text='Указать папку',command=bind).pack(side='left')
            ttk.Button(bar,text='История',command=lambda:self.show_history(rid)).pack(side='left')
            if r['kind']=='order':
                def renumber():
                    n=simpledialog.askstring('Номер','Новый пятизначный номер:',initialvalue=f"{r['num']:05d}",parent=w)
                    if n and messagebox.askyesno('Изменить номер и имя папки',details+f'\nНовый номер: {n}\nСодержимое папки сохраняется. Продолжить?',parent=w):
                        self.run(lambda:self.c.rename_order(rid,n,True),saved)
                ttk.Button(w,text='Изменить номер заявки и переименовать папку',command=renumber).pack(pady=5)
            def delete():
                if messagebox.askyesno('Удалить запись и файлы',details+'\n\nБудут удалены запись и ВСЕ файлы её собственной папки. Связанная запись и её папка сохранятся. Удалить?',parent=w):
                    def deleted(ok):
                        if ok:saved(None)
                        else:w.destroy();self.card(rid)
                    self.run(lambda:self.c.delete(rid,True),deleted)
            if r['status']!=CLOSED:ttk.Button(w,text='Удалить запись вместе с её папкой',command=delete).pack(pady=10)
        self.run(work,done)

    def build_report(self):
        frame=ttk.Frame(self.tabs,padding=8);self.tabs.add(frame,text='История заработка')
        bar=ttk.Frame(frame);bar.pack(fill='x')
        self.year=tk.StringVar(value=str(date.today().year));self.month=tk.StringVar(value=str(date.today().month))
        ttk.Label(bar,text='Год').pack(side='left');ttk.Entry(bar,textvariable=self.year,width=6).pack(side='left')
        ttk.Label(bar,text='Месяц').pack(side='left');ttk.Combobox(bar,textvariable=self.month,values=list(map(str,range(1,13))),width=5,state='readonly').pack(side='left')
        ttk.Button(bar,text='Показать месяц',command=self.report).pack(side='left',padx=5)
        ttk.Button(bar,text='Журнал изменений',command=self.show_history).pack(side='left')
        self.period_list=ttk.Combobox(bar,state='readonly',width=12);self.period_list.pack(side='right');self.period_list.bind('<<ComboboxSelected>>',self.pick_period)
        ttk.Label(bar,text='Сохранённые месяцы:').pack(side='right')
        self.total=tk.StringVar();ttk.Label(frame,textvariable=self.total,font=('Segoe UI',14)).pack(anchor='w',pady=10)
        cols=('num','manager','amount','rate','bonus','comment')
        self.report_table=ttk.Treeview(frame,columns=cols,show='headings')
        for c,label in zip(cols,['Заявка','Менеджер','Общая сумма, ₽','Ставка','Мой заработок, ₽','Комментарий']):self.report_table.heading(c,text=label)
        self.report_table.pack(fill='both',expand=True)
        self.report_table.bind('<Double-1>',lambda e:self.card(self.report_table.selection()[0]) if self.report_table.selection() else None)
        ttk.Label(frame,text='Двойной щелчок — карточка заявки и доступ к обеим папкам. Отчёт учитывает исправления сумм и месяца.').pack()
        self.tabs.bind('<<NotebookTabChanged>>',lambda e:self.report() if self.tabs.index('current')==2 else None)

    def pick_period(self,e=None):
        value=self.period_list.get()
        if value:
            y,m=value.split('-');self.year.set(y);self.month.set(str(int(m)));self.report()

    def report(self):
        y,m=self.year.get(),self.month.get()
        def work():
            if not y.isdigit() or not m.isdigit() or not 1<=int(m)<=12:raise CabinetError('Укажите год и месяц.')
            return self.c.report(int(y),int(m)),self.c.periods()
        def done(data):
            (rows,total),periods=data;self.report_table.delete(*self.report_table.get_children())
            for r in rows:self.report_table.insert('','end',iid=r['id'],values=(f"№{r['num']:05d}",r['manager'],r['amount'],'3%',r['earnings'],r['comment']))
            self.total.set(f'{m.zfill(2)}.{y}: заработано {total:,} ₽ · заявок {len(rows)}'.replace(',',' '))
            self.period_list['values']=[f'{y}-{m:02d}' for y,m in periods]
        self.run(work,done)

    def show_history(self,rid=None):
        def done(rows):
            w=tk.Toplevel(self.w);w.title('Журнал изменений');w.geometry('1000x650')
            text=tk.Text(w,wrap='word');text.pack(fill='both',expand=True)
            for r in rows:
                before=json.loads(r['before_json']) if r['before_json'] else {}
                after=json.loads(r['after_json']) if r['after_json'] else {}
                text.insert('end',f"{r['at']} — {r['event']} — №{(after or before).get('num',0):05d}\n")
                labels={'num':'Номер','manager':'Менеджер','status':'Статус','comment':'Комментарий','amount':'Общая сумма, ₽','close_month':'Месяц','close_year':'Год','folder':'Папка','quote_id':'Связь с просчётом'}
                for k,label in labels.items():
                    if before.get(k)!=after.get(k):text.insert('end',f'  {label}: {before.get(k)} → {after.get(k)}\n')
                if before.get('amount')!=after.get('amount'):
                    text.insert('end',f"  Заработок 3%: {earnings(before.get('amount'))} → {earnings(after.get('amount'))} ₽\n")
                text.insert('end','\n')
            text.configure(state='disabled')
        self.run(lambda:self.c.history(rid),done)

    def settings(self):
        w=tk.Toplevel(self.w);w.title('Папки и резервирование');w.geometry('820x350')
        ttk.Label(w,text='Общая папка: '+str(self.c.root)+'\nБаза: '+str(self.c.db_path)+'\nПросчёты: '+str(self.c.root/'Просчеты')+'\nЗаявки: '+str(self.c.root/'Заявки')+'\nКопии: '+str(self.c.root/'Резервные копии'),wraplength=780,justify='left').pack(padx=10,pady=10)
        ttk.Label(w,text='Полная копия базы и файлов каждые 7 дней при работающей программе.\nЕсли программа была закрыта — при следующем запуске. Хранятся максимум 3 копии.\nКопии на том же диске помогают при ошибке, но не при поломке самого диска.').pack(pady=5)
        ttk.Button(w,text='Открыть общую папку',command=lambda:self.run(lambda:open_folder(self.c.root))).pack(pady=5)
        ttk.Button(w,text='Создать копию сейчас',command=lambda:self.run(lambda:self.c.backup(True),lambda p:messagebox.showinfo('Копия создана',p))).pack(pady=5)
        ttk.Label(w,text='Другая база выбирается при запуске с --choose-root.\nДля восстановления используйте запуск с --restore (описано в инструкции).').pack(pady=5)

    def scheduled_backup(self):
        if not self.busy:self.run(lambda:self.c.backup(),lambda p:self.notice.set('Резервная копия: '+p) if p else None,quiet=True)
        self.w.after(60000,self.scheduled_backup)

    def quit(self):
        if self.busy:
            messagebox.showinfo('Операция выполняется','Дождитесь завершения операции перед выходом.');return
        self.pool.shutdown(wait=False);self.c.close();self.w.destroy()


def launch(root=None,choose=False):
    w=tk.Tk();w.withdraw()
    pointer=Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'EngineerCabinet'/'location.json'
    if not root and not choose and pointer.exists():
        try:root=json.loads(pointer.read_text(encoding='utf-8'))['root']
        except (ValueError,KeyError):pass
        if root and not Path(root).is_dir():
            messagebox.showwarning('База недоступна',f'Предыдущая папка недоступна: {root}\nВыберите её новое расположение или новую базу.');root=None
    if not root:
        default=r'D:\Кабинет инженера' if os.name=='nt' else str(Path.home()/'Кабинет инженера')
        root=simpledialog.askstring('Общая папка кабинета','Вся база, заявки, просчёты и копии будут внутри этой папки.\nВведите путь (папка будет создана):',initialvalue=default,parent=w)
        if not root:w.destroy();return
    try:
        cabinet=Cabinet(root)
        pointer.parent.mkdir(parents=True,exist_ok=True)
        pointer.write_text(json.dumps({'root':str(cabinet.root)},ensure_ascii=False),encoding='utf-8')
    except Exception as e:
        messagebox.showerror('Не удалось открыть кабинет',str(e));w.destroy();return
    w.deiconify();App(w,cabinet);w.mainloop()

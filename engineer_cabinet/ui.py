"""Local cabinet interface based on the approved October 2026 visual."""
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
from .widgets import RecordTable, BG, INK, BLUE, MUTED, LINE, MONTHS, rub, month_number


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
        self.w.title('Личный кабинет инженера')
        self.w.geometry('1440x940');self.w.minsize(1080,680);self.w.configure(bg=BG)
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.results=queue.Queue();self.busy=False;self.tables={};self.filters={};self.cache={}
        self.pending_refresh=False;self.pending_report=False;self.search_job=None
        self.notice=tk.StringVar(value='Готово');self.stats=[];self.current_page=0
        style=ttk.Style(window);style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10),background=BG,foreground=INK)
        style.configure('TFrame',background=BG)
        style.configure('TLabel',background=BG,foreground=INK)
        style.configure('TButton',padding=(15,9),background='white',foreground=INK,bordercolor=LINE,lightcolor='white',darkcolor=LINE)
        style.map('TButton',background=[('active','#eaf2ff')])
        style.configure('Primary.TButton',background=BLUE,foreground='white',bordercolor=BLUE)
        style.map('Primary.TButton',background=[('active','#0057e0')],foreground=[('active','white')])
        style.configure('Nav.TButton',background=BG,foreground=MUTED,borderwidth=0,font=('Segoe UI',12,'bold'),padding=(20,12))
        style.configure('Selected.Nav.TButton',background='#dcecff',foreground=BLUE)
        style.configure('TEntry',padding=9,fieldbackground='white',bordercolor=LINE)
        style.configure('TCombobox',padding=8,fieldbackground='white',background='white',bordercolor=LINE,arrowsize=15)
        style.map('TCombobox',fieldbackground=[('readonly','white')],selectbackground=[('readonly','white')],selectforeground=[('readonly',INK)])
        style.configure('Treeview',rowheight=44,background='white',fieldbackground='white',bordercolor=LINE)
        style.configure('Treeview.Heading',padding=10,background='#eff4fb',foreground=INK)
        style.map('Treeview',background=[('selected','#dcecff')],foreground=[('selected',INK)])
        style.layout('Pages.TNotebook',[]);style.layout('Pages.TNotebook.Tab',[])
        top=ttk.Frame(window,padding=(20,12));top.pack(fill='x')
        self.nav=[]
        for i,text in enumerate(('☷  Заявки','▦  Просчёты','▥  Отчётность','⚙  Настройки')):
            b=ttk.Button(top,text=text,style='Nav.TButton',command=lambda n=i:self.navigate(n))
            b.pack(side='left',padx=(0,8));self.nav.append(b)
        ttk.Button(top,text='＋  Просчёт',style='Primary.TButton',command=lambda:self.create_dialog('quote')).pack(side='right',padx=(10,0))
        ttk.Button(top,text='＋  Заявка',command=lambda:self.create_dialog('order')).pack(side='right')
        ttk.Separator(window).pack(fill='x')
        self.tabs=ttk.Notebook(window,style='Pages.TNotebook');self.tabs.pack(fill='both',expand=True,padx=20,pady=(12,0))
        for kind,title in [('order','Заявки'),('quote','Просчёты')]:self.build_list(kind,title)
        self.build_report();self.build_settings()
        footer=ttk.Frame(window,padding=(22,10));footer.pack(fill='x')
        ttk.Label(footer,textvariable=self.notice,foreground=MUTED).pack(side='left')
        ttk.Label(footer,text='Локальная база • 3%',foreground=MUTED).pack(side='right')
        self.tabs.bind('<<NotebookTabChanged>>',self.page_changed)
        self.w.after(50,self.drain);self.w.protocol('WM_DELETE_WINDOW',self.quit)
        self.navigate(0);self.refresh();self.w.after(2500,self.scheduled_backup)

    def navigate(self,index):
        if any(table.editor for table in self.tables.values()):
            self.notice.set('Сохраните комментарий клавишей Enter или отмените клавишей Esc.');return
        self.tabs.select(index)
        for i,b in enumerate(self.nav):b.configure(style='Selected.Nav.TButton' if i==index else 'Nav.TButton')
        self.current_page=index
        if index==2:self.report()

    def page_changed(self,e=None):
        index=self.tabs.index('current');self.current_page=index
        for i,b in enumerate(self.nav):b.configure(style='Selected.Nav.TButton' if i==index else 'Nav.TButton')
        if index==2:self.report()

    def changed_filter(self,e=None):
        if self.search_job:self.w.after_cancel(self.search_job)
        self.search_job=self.w.after(200,self.refresh)

    def reset_filters(self,kind):
        for var,value in zip(self.filters[kind],('Все','Все менеджеры','','Все','Все статусы')):var.set(value)
        self.refresh()

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
                    self.notice.set('Готово • Данные обновлены')
                    if done: done(value)
                else:
                    self.notice.set(str(value))
                    if not quiet: messagebox.showerror('Операция не выполнена',str(value),parent=self.w)
        except queue.Empty:
            pass
        if not self.busy:
            if self.pending_refresh:
                self.pending_refresh=False;self.refresh()
            elif self.pending_report:
                self.pending_report=False;self.report()
        self.w.after(50,self.drain)

    def build_list(self,kind,title):
        frame=ttk.Frame(self.tabs);self.tabs.add(frame,text=title)
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=(4,18))
        group=tk.StringVar(value='Все');manager=tk.StringVar(value='Все менеджеры')
        suffix=tk.StringVar();link=tk.StringVar(value='Все');status=tk.StringVar(value='Все статусы')
        self.filters[kind]=(group,manager,suffix,link,status)
        groups=['Все','Активные','Готовые']+(['Завершённые'] if kind=='order' else ['С заявкой','Без заявки'])
        for i,(label,var,values) in enumerate((('Поиск по номеру',suffix,None),('Менеджер',manager,['Все менеджеры',*sorted(MANAGERS)]),
                ('Статус',status,['Все статусы',*STATUSES]+([CLOSED] if kind=='order' else [])),('Показать',group,groups))):
            cell=ttk.Frame(bar);cell.grid(row=0,column=i,sticky='ew',padx=(0,16));bar.columnconfigure(i,weight=1)
            ttk.Label(cell,text=label).pack(anchor='w',pady=(0,7))
            widget=ttk.Entry(cell,textvariable=var,width=20) if values is None else ttk.Combobox(cell,textvariable=var,values=values,state='readonly',width=22)
            widget.pack(fill='x');widget.bind('<<ComboboxSelected>>',self.changed_filter)
            if values is None:var.trace_add('write',lambda *a:self.changed_filter())
        ttk.Button(bar,text='⟳  Сбросить фильтры',command=lambda:self.reset_filters(kind)).grid(row=0,column=4,sticky='s')
        stats=ttk.Frame(frame);stats.pack(fill='x',pady=(0,18));stats.columnconfigure(0,weight=1);stats.columnconfigure(1,weight=1)
        bonusbox=tk.Frame(stats,bg='#e7fbee',highlightthickness=1,highlightbackground='#b8efd0')
        bonusbox.grid(row=0,column=0,sticky='nsew',padx=(0,16))
        tk.Label(bonusbox,text='◎',font=('Segoe UI',36),fg='#087e30',bg='#e7fbee').pack(side='left',padx=24)
        body=tk.Frame(bonusbox,bg='#e7fbee');body.pack(side='left',pady=16)
        tk.Label(body,text='Активные бонусы (3%)',bg='#e7fbee',fg='#087e30',font=('Segoe UI',12)).pack(anchor='w')
        bonus=tk.StringVar(value='—');tk.Label(body,textvariable=bonus,bg='#e7fbee',fg='#087e30',font=('Segoe UI',26,'bold')).pack(anchor='w',pady=(3,0))
        panel=tk.Frame(stats,bg='white',highlightthickness=1,highlightbackground=LINE);panel.grid(row=0,column=1,sticky='nsew')
        tk.Label(panel,text='Статистика активных заявок',font=('Segoe UI',10,'bold'),bg='white',fg=INK).pack(anchor='w',padx=16,pady=(10,5))
        cards=tk.Frame(panel,bg='white');cards.pack(fill='both',expand=True,padx=10,pady=(0,10))
        counters={}
        for i,(status,label,bg,fg) in enumerate((('Создан','●  Создано','#f1f4f9',MUTED),('В работе','●  В работе','#eaf4ff',BLUE),('Пауза','●  На паузе','#fff3e5','#e77800'))):
            box=tk.Frame(cards,bg=bg);box.pack(side='left',fill='both',expand=True,padx=5)
            tk.Label(box,text=label,bg=bg,fg=fg,font=('Segoe UI',10)).pack(padx=12,pady=(6,0))
            v=tk.StringVar(value='0');tk.Label(box,textvariable=v,bg=bg,fg=fg,font=('Segoe UI',20,'bold')).pack(pady=(0,5));counters[status]=v
        ttk.Label(frame,text='Бонусы всех незавершённых заявок, включая готовые. Показатели не зависят от фильтров.',foreground=MUTED).pack(anchor='w',pady=(0,10))
        self.stats.append((bonus,counters))
        cols=[('num','№ заявки' if kind=='order' else '№ просчёта',100),('related','Просчёт' if kind=='order' else 'Заявка',90),
              ('created','Дата создания',135),('manager','Менеджер',190)]
        if kind=='order':cols.append(('earnings','Мой бонус',115))
        cols.extend([('comment','Комментарий',250),('status','Статус',170),('actions','Действия',140)])
        tree=RecordTable(frame,cols,lambda action,r,e:self.table_action(kind,action,r,e));tree.pack(fill='both',expand=True)
        self.tables[kind]=tree
        count=ttk.Label(frame,text='',foreground=MUTED);count.pack(anchor='w',pady=(10,4))
        self.cache[kind]={'count':count,'rows':{}}

    def refresh(self):
        self.search_job=None
        if self.busy:self.pending_refresh=True;return
        if any(t.editor for t in self.tables.values()):
            self.notice.set('Enter — сохранить комментарий, Esc — отменить.');return
        args={k:tuple(v.get() for v in self.filters[k]) for k in self.tables}
        def work():
            data={}
            for k,(group,manager,suffix,link,status) in args.items():
                if group in ('С заявкой','Без заявки'):
                    link='Со связью' if group=='С заявкой' else 'Без связи';group='Все'
                rows=self.c.rows(k,group,'' if manager=='Все менеджеры' else manager,suffix,link)
                data[k]=[r for r in rows if status=='Все статусы' or r['status']==status]
            data['all']=self.c.rows('order')
            return data
        def done(data):
            all_orders={r['id']:r for r in data['all']}
            for kind in self.tables:
                rows=data[kind];self.cache[kind]['rows']={r['id']:r for r in rows}
                for r in rows:
                    rel=r['quote_num'] if kind=='order' else r['order_num']
                    r['_related_id']=r['quote_id'] if kind=='order' else next((o['id'] for o in all_orders.values() if o['quote_id']==r['id']),None)
                    r['_cells']={'num':f"{r['num']:05d}",'related':f'{rel:05d}' if rel is not None else '—',
                        'created':date.fromisoformat(r['created']).strftime('%d.%m.%Y'),'manager':r['manager'],
                        'earnings':rub(r['earnings']),'comment':r['comment']}
                self.tables[kind].set_rows(rows)
                text=f"Найдено: {len(rows)} {'заявок' if kind=='order' else 'просчётов'}"
                if kind=='quote':text+=f" · Без заявки: {sum(r['order_num'] is None for r in rows)}"
                self.cache[kind]['count'].configure(text=text+'   •   Комментарий: Enter — сохранить, Esc — отменить')
            active=[r for r in data['all'] if r['status']!=CLOSED]
            for bonus,counters in self.stats:
                bonus.set(rub(sum(r['earnings'] or 0 for r in active)))
                for status,var in counters.items():var.set(str(sum(r['status']==status for r in active)))
        self.run(work,done)

    def table_action(self,kind,action,r,event):
        if action=='refresh':self.refresh();return
        if self.busy:self.notice.set('Дождитесь окончания текущей операции.');return
        rid=r['id']
        if action=='card':self.card(rid)
        elif action=='related':self.card(r['_related_id'])
        elif action=='comment':
            def saved(_):self.tables[kind].cancel_edit();self.refresh()
            self.run(lambda:self.c.update(rid,comment=event),saved)
        elif action=='folder':self.run(lambda:open_folder(self.c.path(r['folder'])))
        elif action=='related_folder':
            def work():return open_folder(self.c.path(self.c.get(r['_related_id'])['folder']))
            self.run(work)
        elif action in ('menu','status'):
            menu=tk.Menu(self.w,tearoff=0,font=('Segoe UI',11),bg='white',fg=INK,activebackground='#e2f0ff',activeforeground=BLUE)
            if action=='status':
                if r['status']==CLOSED:menu.add_command(label='Изменить месяц закрытия…',command=lambda:self.card(rid))
                else:
                    for status in STATUSES:
                        menu.add_command(label=status,command=lambda v=status:self.run(lambda:self.c.update(rid,status=v),lambda _:self.refresh()))
                    if kind=='order':menu.add_separator();menu.add_command(label='Завершить — выбрать месяц…',command=lambda:self.card(rid))
            else:
                menu.add_command(label='Открыть карточку',command=lambda:self.card(rid))
                if kind=='order':
                    menu.add_command(label='Изменить сумму',command=lambda:self.card(rid,'amount'))
                    menu.add_command(label='Изменить менеджера',command=lambda:self.card(rid,'manager'))
                menu.add_separator()
                menu.add_command(label='Открыть папку '+('заявки' if kind=='order' else 'просчёта'),command=lambda:self.table_action(kind,'folder',r,None))
                menu.add_command(label='Открыть папку '+('просчёта' if kind=='order' else 'заявки'),state='normal' if r.get('_related_id') else 'disabled',command=lambda:self.table_action(kind,'related_folder',r,None))
            try:menu.tk_popup(event.x_root,event.y_root)
            finally:menu.grab_release()

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

    def card(self,rid,focus=None):
        if self.busy:return
        def work():
            return self.c.get(rid),self.c.rows('quote'),self.c.details(rid)
        def done(data):
            r,quotes,details=data
            w=tk.Toplevel(self.w);w.title(f"Карточка №{r['num']:05d}");w.geometry('900x830');w.configure(bg=BG);w.transient(self.w)
            vars={k:tk.StringVar(value='' if r[k] is None else str(r[k])) for k in ['manager','status','comment','amount','close_month']}
            ttk.Label(w,text=f"Создана: {r['created']}    Год закрытия: {r['close_year'] or '—'}").pack(pady=4)
            if r['close_month'] is not None:vars['close_month'].set(MONTHS[r['close_month']-1])
            man=self.field(w,'Менеджер',vars['manager'],MANAGERS)
            if focus=='manager':man.focus_set()
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
                amount_entry=self.field(w,'Общая сумма, ₽ (для расчёта)',vars['amount'])
                if focus=='amount':amount_entry.focus_set();amount_entry.selection_range(0,'end')
                self.field(w,'Месяц закрытия',vars['close_month'],['',*MONTHS] if r['status']!=CLOSED else MONTHS)
                ttk.Label(w,text=f"Мой бонус: {earnings(r['amount']) if r['amount'] is not None else '—'} ₽; ставка 3%").pack()
            pathbox=tk.Text(w,height=7,wrap='word');pathbox.pack(fill='x',padx=8,pady=5);pathbox.insert('1.0',details+'\n'+r['folder_error']);pathbox.configure(state='disabled')
            def saved(_):
                w.destroy()
                self.pending_report=self.tabs.index('current')==2
                self.refresh()
            def save():
                if self.busy:return
                fields={k:v.get() for k,v in vars.items() if r['kind']=='order' or k in ('status','comment')}
                if r['kind']=='order':fields['month']=month_number(fields.pop('close_month'));fields['quote_id']=choices[qvar.get()]
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
        frame=ttk.Frame(self.tabs,padding=(0,6));self.tabs.add(frame,text='Отчётность')
        ttk.Label(frame,text='Отчётность',font=('Segoe UI',20,'bold')).pack(anchor='w',pady=(0,5))
        ttk.Label(frame,text='История заработка по завершённым заявкам',foreground=MUTED).pack(anchor='w',pady=(0,20))
        bar=ttk.Frame(frame);bar.pack(fill='x')
        self.year=tk.StringVar(value=str(date.today().year));self.month=tk.StringVar(value=MONTHS[date.today().month-1])
        ttk.Label(bar,text='Год').pack(side='left',padx=(0,8));ttk.Entry(bar,textvariable=self.year,width=6).pack(side='left')
        ttk.Label(bar,text='Месяц').pack(side='left',padx=(20,8))
        combo=ttk.Combobox(bar,textvariable=self.month,values=MONTHS,width=15,state='readonly');combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>',lambda e:self.report())
        ttk.Button(bar,text='Показать',style='Primary.TButton',command=self.report).pack(side='left',padx=12)
        ttk.Button(bar,text='Журнал изменений',command=self.show_history).pack(side='right')
        self.period_list=ttk.Combobox(bar,state='readonly',width=20);self.period_list.pack(side='right',padx=15);self.period_list.bind('<<ComboboxSelected>>',self.pick_period)
        self.period_choices={}
        self.total=tk.StringVar(value='Выберите месяц')
        tk.Label(frame,textvariable=self.total,font=('Segoe UI',22,'bold'),bg='#e7fbee',fg='#087e30',anchor='w',padx=22,pady=22).pack(fill='x',pady=20)
        cols=('num','manager','amount','rate','bonus','comment')
        wrap=ttk.Frame(frame);wrap.pack(fill='both',expand=True)
        self.report_table=ttk.Treeview(wrap,columns=cols,show='headings',selectmode='browse')
        for c,label,width in zip(cols,['Заявка','Менеджер','Общая сумма, ₽','Ставка','Мой заработок, ₽','Комментарий'],[100,210,160,85,175,310]):
            self.report_table.heading(c,text=label);self.report_table.column(c,width=width,stretch=c=='comment',minwidth=70)
        scroll=ttk.Scrollbar(wrap,orient='vertical',command=self.report_table.yview);scroll.pack(side='right',fill='y')
        self.report_table.configure(yscrollcommand=scroll.set);self.report_table.pack(fill='both',expand=True)
        self.report_table.bind('<Double-1>',lambda e:self.card(self.report_table.selection()[0]) if self.report_table.selection() else None)
        actions=ttk.Frame(frame);actions.pack(fill='x',pady=12)
        def card():
            selected=self.report_table.selection()
            if selected:self.card(selected[0])
        ttk.Button(actions,text='Открыть карточку и папки',command=card).pack(side='left')
        ttk.Label(actions,text='Итог учитывает исправления суммы и месяца',foreground=MUTED).pack(side='right')

    def pick_period(self,e=None):
        period=self.period_choices.get(self.period_list.get())
        if period:
            y,m=period;self.year.set(str(y));self.month.set(MONTHS[m-1]);self.report()

    def report(self):
        if self.busy:self.pending_report=True;return
        y=self.year.get();m=month_number(self.month.get())
        def work():
            if not y.isdigit() or not 1<=int(y)<=9999 or m is None:raise CabinetError('Укажите год и месяц.')
            return self.c.report(int(y),m),self.c.periods()
        def done(data):
            (rows,total),periods=data;self.report_table.delete(*self.report_table.get_children())
            for r in rows:self.report_table.insert('','end',iid=r['id'],values=(f"{r['num']:05d}",r['manager'],rub(r['amount']),'3%',rub(r['earnings']),r['comment']))
            self.total.set(f'{MONTHS[m-1]} {y}   •   {rub(total)}   •   Заявок: {len(rows)}')
            self.period_choices={f'{MONTHS[month-1]} {year}':(year,month) for year,month in periods}
            self.period_list['values']=list(self.period_choices)
            self.period_list.set(f'{MONTHS[m-1]} {y}' if (int(y),m) in periods else '')
        self.run(work,done)

    def build_settings(self):
        frame=ttk.Frame(self.tabs,padding=(0,8));self.tabs.add(frame,text='Настройки')
        ttk.Label(frame,text='Настройки',font=('Segoe UI',20,'bold')).pack(anchor='w',pady=(0,10))
        ttk.Label(frame,text='Папки кабинета',font=('Segoe UI',13,'bold')).pack(anchor='w',pady=12)
        for label,path in [('Общая папка',self.c.root),('Данные',self.c.db_path.parent),('Заявки',self.c.root/'Заявки'),('Просчёты',self.c.root/'Просчеты'),('Резервные копии',self.c.root/'Резервные копии')]:
            row=ttk.Frame(frame,padding=(0,6));row.pack(fill='x')
            ttk.Label(row,text=label,width=22).pack(side='left')
            var=tk.StringVar(value=str(path));ttk.Entry(row,textvariable=var,state='readonly').pack(side='left',fill='x',expand=True,padx=12)
            ttk.Button(row,text='Открыть папку',command=lambda p=path:self.run(lambda:open_folder(p))).pack(side='right')
        ttk.Label(frame,text='Резервные копии',font=('Segoe UI',13,'bold')).pack(anchor='w',pady=(28,12))
        ttk.Label(frame,text='Каждые 7 дней • Хранятся 3 полные копии базы и файлов.\nЕсли кабинет закрыт, проверка выполняется при следующем запуске.').pack(anchor='w',pady=8)
        ttk.Button(frame,text='Создать копию сейчас',style='Primary.TButton',command=lambda:self.run(lambda:self.c.backup(True),lambda p:messagebox.showinfo('Копия создана',p))).pack(anchor='w',pady=10)
        ttk.Label(frame,text='Другая база: запуск с --choose-root. Восстановление: запуск с --restore.\nПодробный порядок — в READ_ME.md рядом с программой.',foreground=MUTED).pack(anchor='w',pady=18)

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

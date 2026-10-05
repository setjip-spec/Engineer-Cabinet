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

from . import __version__
from .dialogs import Dialog
from .core import Cabinet, CabinetError, MANAGERS, STATUSES, CLOSED, earnings
from .widgets import RecordTable, BG, INK, BLUE, MUTED, LINE, MONTHS, STATUS_COLORS, rub, month_number


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
        self.w.title(f'Личный кабинет инженера — {__version__}')
        self.w.geometry('1440x940');self.w.minsize(1000,620);self.w.configure(bg=BG)
        if sys.platform=='win32':self.w.state('zoomed')
        else:
            try:self.w.attributes('-zoomed',True)
            except tk.TclError:pass
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.results=queue.Queue();self.busy=False;self.tables={};self.filters={};self.status_filters={};self.cache={}
        self.saved_filters=self.c.preference('filters_v1',{}) or {}
        self.pending_refresh=False;self.pending_report=False;self.search_job=None
        self.notice=tk.StringVar(value='Готово');self.stats=[];self.ready_bonuses=[];self.current_page=0
        style=ttk.Style(window);style.theme_use('clam')
        style.configure('.',font=('Segoe UI',11),background=BG,foreground=INK)
        style.configure('TFrame',background=BG)
        style.configure('TLabel',background=BG,foreground=INK)
        style.configure('TButton',padding=(15,9),background='white',foreground=INK,bordercolor=LINE,lightcolor='white',darkcolor=LINE)
        style.map('TButton',background=[('active','#eaf2ff')])
        style.configure('Primary.TButton',background=BLUE,foreground='white',bordercolor=BLUE)
        style.map('Primary.TButton',background=[('active','#0057e0')],foreground=[('active','white')])
        style.configure('Danger.TButton',foreground='#b42318',bordercolor='#f2c6c2',background='#fff4f2')
        style.map('Danger.TButton',background=[('active','#ffe2df')])
        style.configure('Nav.TButton',background=BG,foreground=MUTED,borderwidth=0,font=('Segoe UI',12,'bold'),padding=(20,12))
        style.configure('Selected.Nav.TButton',background='#dcecff',foreground=BLUE)
        style.configure('TEntry',padding=9,fieldbackground='white',bordercolor=LINE)
        style.configure('TCombobox',padding=8,fieldbackground='white',background='white',bordercolor=LINE,arrowsize=15)
        style.map('TCombobox',foreground=[('disabled',MUTED),('readonly',INK)],fieldbackground=[('readonly','white')],selectbackground=[('readonly','white')],selectforeground=[('readonly',INK)])
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
        ttk.Label(footer,text=f'Версия {__version__} • Локальная база • 3%',foreground=MUTED).pack(side='right')
        self.tabs.bind('<<NotebookTabChanged>>',self.page_changed)
        self.w.after(50,self.drain);self.w.protocol('WM_DELETE_WINDOW',self.quit)
        self.navigate(0);self.refresh();self.w.after(2500,self.scheduled_backup)

    def navigate(self,index):
        if any(table.editor for table in [*self.tables.values(),self.report_table]):
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

    def status_filter_changed(self,kind,trigger=True):
        data=self.status_filters[kind]
        selected=[s for s,v in data['vars'].items() if v.get()]
        if len(selected)==len(data['choices']):
            label='Все статусы'
        elif not selected:
            label='Нет статусов'
        elif len(selected)<=2:
            label=', '.join(selected)
        else:
            label=f'Выбрано: {len(selected)}'
        data['label'].set(label)
        if trigger:self.changed_filter()

    def set_all_status_filters(self,kind,value):
        for var in self.status_filters[kind]['vars'].values():var.set(value)
        self.status_filter_changed(kind)

    def filter_snapshot(self):
        state={}
        for kind,(group,manager,suffix,link,comment) in self.filters.items():
            state[kind]={
                'group':group.get(),'manager':manager.get(),'suffix':suffix.get(),
                'link':link.get(),'comment':comment.get(),
                'statuses':[s for s,v in self.status_filters[kind]['vars'].items() if v.get()]
            }
        return state

    def save_filter_state(self):
        self.c.set_preference('filters_v1',self.filter_snapshot())

    def reset_filters(self,kind):
        group,manager,suffix,link,comment=self.filters[kind]
        group.set('Все');manager.set('Все менеджеры');suffix.set('');link.set('Все');comment.set('')
        for var in self.status_filters[kind]['vars'].values():var.set(True)
        self.status_filter_changed(kind)
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
        saved=self.saved_filters.get(kind,{}) if isinstance(self.saved_filters,dict) else {}
        groups=['Все','Активные']+(['Активные + готовые'] if kind=='order' else [])+['Готовые']+(['Завершённые'] if kind=='order' else ['С заявкой','Без заявки'])
        group_value=saved.get('group','Все');group_value=group_value if group_value in groups else 'Все'
        manager_value=saved.get('manager','Все менеджеры');manager_value=manager_value if manager_value in ['Все менеджеры',*MANAGERS] else 'Все менеджеры'
        group=tk.StringVar(value=group_value);manager=tk.StringVar(value=manager_value)
        suffix=tk.StringVar(value=str(saved.get('suffix','') or ''));link=tk.StringVar(value='Все')
        comment=tk.StringVar(value=str(saved.get('comment','') or ''));self.filters[kind]=(group,manager,suffix,link,comment)
        status_choices=[*STATUSES]+([CLOSED] if kind=='order' else [])
        saved_statuses=saved.get('statuses',status_choices)
        if not isinstance(saved_statuses,list):saved_statuses=status_choices
        status_vars={s:tk.BooleanVar(value=s in saved_statuses) for s in status_choices}
        status_label=tk.StringVar(value='Все статусы')
        self.status_filters[kind]={'choices':status_choices,'vars':status_vars,'label':status_label}
        for i,(label,var,values) in enumerate((('Поиск по номеру',suffix,None),('Поиск по комментарию',comment,None),('Менеджер',manager,['Все менеджеры',*sorted(MANAGERS)]),
                ('Статус',None,None),('Показать',group,groups))):
            cell=ttk.Frame(bar);cell.grid(row=0,column=i,sticky='ew',padx=(0,12));bar.columnconfigure(i,weight=1,uniform='filters')
            ttk.Label(cell,text=label).pack(anchor='w',pady=(0,7))
            if label=='Статус':
                widget=ttk.Menubutton(cell,textvariable=status_label)
                menu=tk.Menu(widget,tearoff=False)
                for status_name in status_choices:
                    menu.add_checkbutton(label=status_name,variable=status_vars[status_name],command=lambda k=kind:self.status_filter_changed(k))
                menu.add_separator()
                menu.add_command(label='Выбрать все',command=lambda k=kind:self.set_all_status_filters(k,True))
                menu.add_command(label='Снять все',command=lambda k=kind:self.set_all_status_filters(k,False))
                widget.configure(menu=menu)
                widget.pack(fill='x')
                self.status_filter_changed(kind,False)
            else:
                widget=ttk.Entry(cell,textvariable=var,width=12) if values is None else ttk.Combobox(cell,textvariable=var,values=values,state='readonly',width=16)
                widget.pack(fill='x');widget.bind('<<ComboboxSelected>>',self.changed_filter)
                if values is None:var.trace_add('write',lambda *a:self.changed_filter())
        ttk.Button(bar,text='⟳  Сбросить фильтры',command=lambda:self.reset_filters(kind)).grid(row=0,column=5,sticky='s')
        stats=ttk.Frame(frame);stats.pack(fill='x',pady=(0,18));stats.columnconfigure(0,weight=1);stats.columnconfigure(1,weight=1)
        bonusbox=tk.Frame(stats,bg='#e7fbee',highlightthickness=1,highlightbackground='#b8efd0')
        bonusbox.grid(row=0,column=0,sticky='nsew',padx=(0,16))
        tk.Label(bonusbox,text='Бонусы · 3%',bg='#e7fbee',fg='#087e30',font=('Segoe UI',11,'bold')).pack(anchor='w',padx=18,pady=(10,0))
        body=tk.Frame(bonusbox,bg='#e7fbee');body.pack(fill='both',expand=True,padx=16,pady=(6,12))
        bonus=tk.StringVar(value='—');ready_bonus=tk.StringVar(value='—');self.ready_bonuses.append(ready_bonus)
        for i,(label,var) in enumerate((('Активные',bonus),('Готовые',ready_bonus))):
            box=tk.Frame(body,bg='#e7fbee');box.grid(row=0,column=i*2,sticky='nsew',padx=8)
            body.columnconfigure(i*2,weight=1,uniform='bonuses')
            tk.Label(box,text=label,bg='#e7fbee',fg='#087e30',font=('Segoe UI',12)).pack(anchor='w')
            tk.Label(box,textvariable=var,bg='#e7fbee',fg='#087e30',font=('Segoe UI',25,'bold')).pack(anchor='w',pady=(3,0))
        tk.Frame(body,bg='#b8e8c9',width=1).grid(row=0,column=1,sticky='ns',padx=14)
        panel=tk.Frame(stats,bg='white',highlightthickness=1,highlightbackground=LINE);panel.grid(row=0,column=1,sticky='nsew')
        tk.Label(panel,text='Статистика активных заявок',font=('Segoe UI',10,'bold'),bg='white',fg=INK).pack(anchor='w',padx=16,pady=(10,5))
        cards=tk.Frame(panel,bg='white');cards.pack(fill='both',expand=True,padx=10,pady=(0,10))
        counters={}
        for i,(status,label,bg,fg) in enumerate((('Создан','●  Создано','#f1f4f9',MUTED),('В работе','●  В работе','#eaf4ff',BLUE),('Пауза','●  На паузе','#fff3e5','#e77800'))):
            box=tk.Frame(cards,bg=bg);box.pack(side='left',fill='both',expand=True,padx=5)
            tk.Label(box,text=label,bg=bg,fg=fg,font=('Segoe UI',10)).pack(padx=12,pady=(6,0))
            v=tk.StringVar(value='0');tk.Label(box,textvariable=v,bg=bg,fg=fg,font=('Segoe UI',20,'bold')).pack(pady=(0,5));counters[status]=v
        ttk.Label(frame,text='Бонусы по заявкам: активные и готовые отдельно. Завершённые — в отчётности. Показатели не зависят от фильтров.',foreground=MUTED).pack(anchor='w',pady=(0,10))
        self.stats.append((bonus,counters))
        cols=[('num','№ заявки' if kind=='order' else '№ просчёта',100 if kind=='order' else 125),('related','Просчёт' if kind=='order' else 'Заявка',90),
              ('created','Дата создания',135),('manager','Менеджер',190)]
        if kind=='order':cols.append(('earnings','Мой бонус',115))
        cols.extend([('comment','Комментарий',250),('status','Статус',170),('actions','Действия',140)])
        tree=RecordTable(frame,cols,lambda action,r,e:self.table_action(kind,action,r,e),can_edit=lambda:not self.busy);tree.pack(fill='both',expand=True)
        self.tables[kind]=tree
        count=ttk.Label(frame,text='',foreground=MUTED);count.pack(anchor='w',pady=(10,4))
        self.cache[kind]={'count':count,'rows':{}}

    def refresh(self):
        self.search_job=None
        if self.busy:self.pending_refresh=True;return
        if any(t.editor for t in self.tables.values()):
            self.notice.set('Enter — сохранить комментарий, Esc — отменить.');return
        self.save_filter_state()
        args={k:tuple(v.get() for v in self.filters[k]) for k in self.tables}
        selected_statuses={k:{s for s,v in self.status_filters[k]['vars'].items() if v.get()} for k in self.tables}
        def work():
            data={}
            for k,(group,manager,suffix,link,comment) in args.items():
                if group in ('С заявкой','Без заявки'):
                    link='Со связью' if group=='С заявкой' else 'Без связи';group='Все'
                rows=self.c.rows(k,group,'' if manager=='Все менеджеры' else manager,suffix,link,comment=comment)
                data[k]=[r for r in rows if r['status'] in selected_statuses[k]]
            data['all']=self.c.rows('order')
            return data
        def done(data):
            order_by_quote={r['quote_id']:r['id'] for r in data['all'] if r['quote_id']}
            for kind in self.tables:
                rows=data[kind];self.cache[kind]['rows']={r['id']:r for r in rows}
                for r in rows:
                    rel=r['quote_num'] if kind=='order' else r['order_num']
                    r['_related_id']=r['quote_id'] if kind=='order' else order_by_quote.get(r['id'])
                    r['_cells']={'num':f"{r['num']:05d}",'related':f'{rel:05d}' if rel is not None else '—',
                        'created':date.fromisoformat(r['created']).strftime('%d.%m.%Y'),'manager':r['manager'],
                        'earnings':rub(r['earnings']),'comment':r['comment']}
                self.tables[kind].set_rows(rows)
                text=f"Найдено: {len(rows)} {'заявок' if kind=='order' else 'просчётов'}"
                if kind=='quote':text+=f" · Без заявки: {sum(r['order_num'] is None for r in rows)}"
                self.cache[kind]['count'].configure(text=text+'   •   Комментарий: Enter — сохранить, Esc — отменить')
            active=[r for r in data['all'] if r['status'] in STATUSES[:3]]
            ready_total=sum(r['earnings'] or 0 for r in data['all'] if r['status']=='Готово')
            for ready_bonus in self.ready_bonuses:ready_bonus.set(rub(ready_total))
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
        elif action=='status':self.status_dialog(rid)
        elif action=='menu':
            menu=self.record_menu(kind,r)
            try:menu.tk_popup(event.x_root,event.y_root)
            finally:menu.grab_release()

    def record_menu(self,kind,r):
        rid=r['id']
        menu=tk.Menu(self.w,tearoff=0,font=('Segoe UI',11),bg='white',fg=INK,activebackground='#e2f0ff',activeforeground=BLUE)
        menu.add_command(label='Открыть карточку',command=lambda:self.card(rid))
        if kind=='order':
            menu.add_command(label='Изменить сумму',command=lambda:self.card(rid,'amount'))
            menu.add_command(label='Изменить менеджера',command=lambda:self.card(rid,'manager'))
        menu.add_separator()
        menu.add_command(label='Открыть папку '+('заявки' if kind=='order' else 'просчёта'),command=lambda:self.table_action(kind,'folder',r,None))
        menu.add_command(label='Открыть папку '+('просчёта' if kind=='order' else 'заявки'),state='normal' if r.get('_related_id') else 'disabled',command=lambda:self.table_action(kind,'related_folder',r,None))
        menu.add_separator()
        menu.add_command(label='Удалить запись и папку…',foreground='#b42318',state='disabled' if r['status']==CLOSED else 'normal',command=lambda:self.delete_dialog(rid))
        return menu

    def status_dialog(self,rid):
        if self.busy:return
        def loaded(r):
            w=Dialog(self.w,f"Статус — №{r['num']:05d}")
            ttk.Label(w.body,text='Выберите статус',font=('Segoe UI',18,'bold')).pack(anchor='w')
            ttk.Label(w.body,text=f"№{r['num']:05d} • {r['manager']}",foreground=MUTED).pack(anchor='w',pady=(5,16))
            options=ttk.Frame(w.body);options.pack(fill='both',expand=True)
            descriptions={'Создан':'Новая запись','В работе':'Занимаюсь заданием','Пауза':'Ожидаю задание или данные','Готово':'Инженерная работа готова',CLOSED:'Заявка закрыта за месяц'}
            statuses=[CLOSED] if r['status']==CLOSED else STATUSES
            for i,status in enumerate(statuses):
                bg,fg=STATUS_COLORS[status]
                def choose(value=status):
                    if self.busy:return
                    self.run(lambda:self.c.update(rid,status=value),lambda _: (w.destroy(),self.refresh()))
                button=tk.Button(options,text=('✓  ' if status==r['status'] else '●  ')+status+'\n'+descriptions[status],
                    font=('Segoe UI',12),bg=bg,fg=fg,activebackground=bg,activeforeground=fg,
                    relief='flat',bd=0,highlightthickness=2,highlightbackground=fg if status==r['status'] else bg,
                    padx=16,pady=18,anchor='w',justify='left',cursor='hand2',command=choose,
                    state='disabled' if status==CLOSED else 'normal')
                button.grid(row=i//2,column=i%2,sticky='nsew',padx=5,pady=5)
            options.columnconfigure(0,weight=1);options.columnconfigure(1,weight=1)
            ttk.Button(w.footer,text='Отмена',command=w.destroy).pack(side='right')
            if r['kind']=='order':
                def closing():w.destroy();self.card(rid,'month')
                ttk.Button(w.footer,text='Изменить месяц закрытия' if r['status']==CLOSED else 'Завершить — выбрать месяц',style='Primary.TButton',command=closing).pack(side='left')
            w.show((660,350 if r['status']==CLOSED else 430))
        self.run(lambda:self.c.get(rid),loaded)

    def delete_dialog(self,rid):
        if self.busy:return
        def loaded(data):
            r,details=data
            if r['status']==CLOSED:
                messagebox.showinfo('Удаление недоступно','Завершённую заявку нельзя удалить.',parent=self.w);return
            w=Dialog(self.w,'Подтвердите удаление',scroll=True)
            ttk.Label(w.body,text=f"Удалить {'заявку' if r['kind']=='order' else 'просчёт'} №{r['num']:05d}?",font=('Segoe UI',18,'bold'),foreground='#b42318').pack(anchor='w',pady=(0,12))
            ttk.Label(w.body,text='Будут удалены запись и ВСЕ файлы её собственной папки.\nСвязанная запись и её папка сохранятся. Очистится только связь.',wraplength=650,justify='left').pack(anchor='w',pady=(0,14))
            text=tk.Text(w.body,height=10,wrap='word',font=('Segoe UI',11),relief='flat',padx=12,pady=12)
            text.pack(fill='both',expand=True);text.insert('1.0',details);text.configure(state='disabled')
            cancel=ttk.Button(w.footer,text='Отмена',command=w.destroy);cancel.pack(side='right',padx=(12,0))
            def confirm():
                if self.busy:return
                def deleted(ok):
                    w.destroy()
                    if ok:
                        for window in self.w.winfo_children():
                            if isinstance(window,tk.Toplevel) and getattr(window,'record_id',None)==rid:window.destroy()
                        self.pending_report=self.tabs.index('current')==2;self.refresh()
                    else:
                        self.pending_refresh=True;self.card(rid)
                self.run(lambda:self.c.delete(rid,True),deleted)
            ttk.Button(w.footer,text='Удалить запись и файлы',style='Danger.TButton',command=confirm).pack(side='left')
            w.show((760,440));w.grab_set();cancel.focus_set()
        self.run(lambda:(self.c.get(rid),self.c.details(rid)),loaded)

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
        w=Dialog(self.w,'Новая заявка' if kind=='order' else 'Новый просчёт',scroll=True)
        manager=tk.StringVar(value=MANAGERS[0]);num=tk.StringVar();quote=tk.StringVar(value='Без просчёта')
        ttk.Label(w.body,text='Новая заявка' if kind=='order' else 'Новый просчёт',font=('Segoe UI',18,'bold')).pack(anchor='w',pady=(0,16))
        manager_combo=self.field(w.body,'Менеджер',manager,MANAGERS)
        if kind=='order':self.field(w.body,'Номер заявки',num)
        choices={'Без просчёта':None}
        if kind=='order':
            quote_combo=self.field(w.body,'Просчёт выбранного менеджера',quote,[])
            def update_quotes(*args):
                choices.clear();choices['Без просчёта']=None
                for r in quotes:
                    if r['order_num'] is None and r['manager']==manager.get():
                        label=f"№{r['num']:05d} — {date.fromisoformat(r['created']).strftime('%d.%m.%Y')} — {r['manager']}"
                        choices[label]=r['id']
                quote_combo.configure(values=list(choices))
                if quote.get() not in choices:quote.set('Без просчёта')
            manager.trace_add('write',update_quotes);update_quotes()
        ttk.Label(w.body,text='Папки создаются автоматически.'+('\nСумма заполняется позже. Просчёт можно связать позже в карточке.' if kind=='order' else ''),foreground=MUTED).pack(anchor='w',pady=16)
        def create():
            if self.busy:return
            m,n,q=manager.get(),num.get(),choices[quote.get()]
            def done(r):
                w.destroy();self.refresh()
                if r['folder_state']=='error':messagebox.showwarning('Запись сохранена, папка не готова',r['folder_error']+'\nОткройте карточку → Повторить.',parent=self.w)
            self.run(lambda:self.c.create(kind,m,n,q),done)
        ttk.Button(w.footer,text='Отмена',command=w.destroy).pack(side='right')
        ttk.Button(w.footer,text='Создать',style='Primary.TButton',command=create).pack(side='right',padx=12)
        w.show((920,410 if kind=='order' else 300));manager_combo.focus_set()

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
            w=Dialog(self.w,f"Карточка №{r['num']:05d}",scroll=True);w.record_id=rid
            body=w.body
            vars={k:tk.StringVar(value='' if r[k] is None else str(r[k])) for k in ['manager','status','comment','amount','close_month']}
            ttk.Label(body,text=f"Создана: {r['created']}    Год закрытия: {r['close_year'] or '—'}").pack(pady=4)
            if r['close_month'] is not None:vars['close_month'].set(MONTHS[r['close_month']-1])
            man=self.field(body,'Менеджер',vars['manager'],MANAGERS)
            if focus=='manager':man.focus_set()
            if r['kind']=='quote':man.configure(state='disabled')
            self.field(body,'Статус',vars['status'],[CLOSED] if r['status']==CLOSED else STATUSES)
            self.field(body,'Комментарий',vars['comment'])
            choices={'Без просчёта':None};selected='Без просчёта'
            qvar=tk.StringVar(value=selected)
            if r['kind']=='order':
                for q in quotes:
                    if q['order_num'] is None or q['id']==r['quote_id']:
                        label=f"№{q['num']:05d} — {date.fromisoformat(q['created']).strftime('%d.%m.%Y')} — {q['manager']}";choices[label]=q['id']
                        if q['id']==r['quote_id']:selected=label
                qvar.set(selected);combo=self.field(body,'Связанный просчёт',qvar,list(choices))
                def picked(e):
                    qid=choices[qvar.get()]
                    if qid:
                        vars['manager'].set(next(q['manager'] for q in quotes if q['id']==qid))
                        note.set('Менеджер подставлен из просчёта. Можно изменить вручную.')
                combo.bind('<<ComboboxSelected>>',picked)
                note=tk.StringVar();ttk.Label(body,textvariable=note,foreground='#174ea6').pack()
                amount_entry=self.field(body,'Общая сумма, ₽ (для расчёта)',vars['amount'])
                if focus=='amount':amount_entry.focus_set();amount_entry.selection_range(0,'end')
                month_combo=self.field(body,'Месяц закрытия',vars['close_month'],['',*MONTHS] if r['status']!=CLOSED else MONTHS)
                ttk.Label(body,text=f"Мой бонус: {earnings(r['amount']) if r['amount'] is not None else '—'} ₽; ставка 3%").pack()
            pathbox=tk.Text(body,height=7,wrap='word');pathbox.pack(fill='x',padx=8,pady=5);pathbox.insert('1.0',details+'\n'+r['folder_error']);pathbox.configure(state='disabled')
            def saved(_):
                w.destroy()
                self.pending_report=self.tabs.index('current')==2
                self.refresh()
            def save():
                if self.busy:return
                fields={k:v.get() for k,v in vars.items() if r['kind']=='order' or k in ('status','comment')}
                if r['kind']=='order':fields['month']=month_number(fields.pop('close_month'));fields['quote_id']=choices[qvar.get()]
                self.run(lambda:self.c.update(rid,**fields),saved)
            ttk.Button(w.footer,text='Закрыть',command=w.destroy).pack(side='right')
            ttk.Button(w.footer,text='Сохранить',style='Primary.TButton',command=save).pack(side='right',padx=12)
            bar=ttk.Frame(body);bar.pack(fill='x',padx=5)
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
                ttk.Button(body,text='Изменить номер заявки и переименовать папку',command=renumber).pack(pady=5)
            ttk.Button(w.footer,text='Удалить запись и папку…',style='Danger.TButton',
                state='disabled' if r['status']==CLOSED else 'normal',command=lambda:self.delete_dialog(rid)).pack(side='left')
            w.show((960,820))
            if focus=='manager':man.focus_set()
            elif focus=='amount' and r['kind']=='order':amount_entry.focus_set();amount_entry.selection_range(0,'end')
            elif focus=='month' and r['kind']=='order':month_combo.focus_set()
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
        cols=[('num','Заявка',100),('manager','Менеджер',210),('amount','Общая сумма',160),
              ('rate','Ставка',90),('bonus','Мой заработок',175),('comment','Комментарий',260),('actions','Действия',140)]
        self.report_table=RecordTable(frame,cols,self.report_action,can_edit=lambda:not self.busy)
        self.report_table.pack(fill='both',expand=True)
        actions=ttk.Frame(frame);actions.pack(fill='x',pady=12)
        def card():
            selected=self.report_table.selection()
            if selected:self.card(selected[0])
        ttk.Button(actions,text='Открыть карточку и папки',command=card).pack(side='left')
        ttk.Label(actions,text='Итог учитывает исправления суммы и месяца',foreground=MUTED).pack(side='right')

    def report_action(self,action,r,event):
        if action=='refresh':self.report();return
        if action=='comment':
            if self.busy:return
            def saved(_):
                self.report_table.cancel_edit();self.pending_refresh=True;self.report()
            self.run(lambda:self.c.update(r['id'],comment=event),saved)
        else:self.table_action('order',action,r,event)

    def pick_period(self,e=None):
        period=self.period_choices.get(self.period_list.get())
        if period:
            y,m=period;self.year.set(str(y));self.month.set(MONTHS[m-1]);self.report()

    def report(self):
        if self.busy:self.pending_report=True;return
        if self.report_table.editor:
            self.notice.set('Enter — сохранить комментарий, Esc — отменить.');return
        y=self.year.get();m=month_number(self.month.get())
        def work():
            if not y.isdigit() or not 1<=int(y)<=9999 or m is None:raise CabinetError('Укажите год и месяц.')
            return self.c.report(int(y),m),self.c.periods()
        def done(data):
            (rows,total),periods=data
            for r in rows:
                r['_related_id']=r['quote_id']
                r['_cells']={'num':f"{r['num']:05d}",'manager':r['manager'],'amount':rub(r['amount']),
                    'rate':'3%','bonus':rub(r['earnings']),'comment':r['comment']}
            self.report_table.set_rows(rows)
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
            entry=ttk.Entry(row);entry.insert(0,str(path));entry.configure(state='readonly')
            entry.pack(side='left',fill='x',expand=True,padx=12)
            ttk.Button(row,text='Открыть папку',command=lambda p=path:self.run(lambda:open_folder(p))).pack(side='right')
        ttk.Label(frame,text='Резервные копии',font=('Segoe UI',13,'bold')).pack(anchor='w',pady=(28,12))
        ttk.Label(frame,text='Каждые 7 дней • Хранятся 3 полные копии базы и файлов.\nЕсли кабинет закрыт, проверка выполняется при следующем запуске.').pack(anchor='w',pady=8)
        ttk.Button(frame,text='Создать копию сейчас',style='Primary.TButton',command=lambda:self.run(lambda:self.c.backup(True),lambda p:messagebox.showinfo('Копия создана',p))).pack(anchor='w',pady=10)
        ttk.Label(frame,text='Другая база: запуск с --choose-root. Восстановление: запуск с --restore.\nПодробный порядок — в READ_ME.md рядом с программой.',foreground=MUTED).pack(anchor='w',pady=18)

    def show_history(self,rid=None):
        def done(rows):
            w=Dialog(self.w,'Журнал изменений')
            text=tk.Text(w.body,wrap='word');text.pack(fill='both',expand=True)
            ttk.Button(w.footer,text='Закрыть',command=w.destroy).pack(side='right')
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
            text.configure(state='disabled');w.show((1000,650))
        self.run(lambda:self.c.history(rid),done)

    def scheduled_backup(self):
        if not self.busy:self.run(lambda:self.c.backup(),lambda p:self.notice.set('Резервная копия: '+p) if p else None,quiet=True)
        self.w.after(60000,self.scheduled_backup)

    def quit(self):
        if self.busy:
            messagebox.showinfo('Операция выполняется','Дождитесь завершения операции перед выходом.');return
        self.save_filter_state()
        for timer in self.w.tk.call('after','info'):self.w.after_cancel(timer)
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

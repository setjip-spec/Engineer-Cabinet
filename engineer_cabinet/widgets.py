"""Desktop visual components, drawn only for visible rows (no per-record widgets)."""
import tkinter as tk
from tkinter import ttk
from tkinter.font import Font

BG = '#f5f8fd'
INK = '#18244c'
BLUE = '#0665ff'
MUTED = '#647294'
LINE = '#e0e8f5'
MONTHS = ('Январь','Февраль','Март','Апрель','Май','Июнь',
          'Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь')
STATUS_COLORS = {'Создан':('#f0f3f8','#647294'),
                 'В работе':('#e3f1ff','#0665ff'),
                 'Пауза':('#fff0df','#e77800'),
                 'Готово':('#e4fced','#08983d'),
                 'Завершённая':('#d4f4df','#08762a')}


def rub(value):
    return '—' if value is None else f'{value:,} ₽'.replace(',',' ')


def month_number(value):
    if value in MONTHS:
        return MONTHS.index(value)+1
    if str(value).isdigit() and 1 <= int(value) <= 12:
        return int(value)
    return None


def roundrect(c, x1,y1,x2,y2, radius=8, **kw):
    r=min(radius,(x2-x1)/2,(y2-y1)/2)
    return c.create_polygon(x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,
        x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1,
        smooth=True, **kw)


def icon(c,kind,x,y,color=INK):
    if kind=='folder':
        c.create_line(x-8,y+6,x-8,y-6,x-2,y-6,x+1,y-3,x+9,y-3,x+7,y+6,x-8,y+6,fill=color,width=1.6)
    elif kind=='calc':
        c.create_rectangle(x-6,y-9,x+6,y+9,outline=color,width=1.5)
        c.create_rectangle(x-3,y-6,x+3,y-2,outline=color)
        for dx in (-3,2):
            for dy in (2,5): c.create_rectangle(x+dx,y+dy,x+dx+1,y+dy+1,fill=color,outline=color)
    else:
        for dy in (-5,0,5): c.create_oval(x-1,y+dy-1,x+1,y+dy+1,fill=color,outline=color)


class RecordTable(ttk.Frame):
    """Virtual canvas grid with inline editing, status pills and independent actions."""
    def __init__(self,parent,columns,on_action):
        super().__init__(parent)
        self.columns=columns;self.on_action=on_action;self.rows=[];self.selected_id=None
        self.editor=None;self.hits=[];self.offset=0;self.start=0
        self.font=Font(family='Segoe UI',size=10)
        self.bold=Font(family='Segoe UI',size=10,weight='bold')
        self.rh=max(48,self.font.metrics('linespace')+26);self.hh=self.rh-6
        self.header=tk.Canvas(self,height=self.hh,bg='#f0f4fa',highlightthickness=0)
        self.header.grid(row=0,column=0,sticky='ew')
        self.canvas=tk.Canvas(self,bg='white',highlightthickness=0,takefocus=True)
        self.canvas.grid(row=1,column=0,sticky='nsew')
        self.vbar=ttk.Scrollbar(self,orient='vertical',command=self.scroll)
        self.vbar.grid(row=1,column=1,sticky='ns')
        self.hbar=ttk.Scrollbar(self,orient='horizontal',command=self.hscroll)
        self.hbar.grid(row=2,column=0,sticky='ew')
        self.rowconfigure(1,weight=1);self.columnconfigure(0,weight=1)
        self.canvas.bind('<Configure>',lambda e:self.draw())
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<MouseWheel>',lambda e:self.scroll('scroll',-int(e.delta/120),'units'))
        self.canvas.bind('<Button-4>',lambda e:self.scroll('scroll',-1,'units'))
        self.canvas.bind('<Button-5>',lambda e:self.scroll('scroll',1,'units'))
        self.canvas.bind('<Up>',lambda e:self.move(-1));self.canvas.bind('<Down>',lambda e:self.move(1))
        self.canvas.bind('<Return>',lambda e:self.activate('card'))
        self.canvas.bind('<F2>',lambda e:self.edit_selected())
        self.canvas.bind('<F5>',lambda e:self.on_action('refresh',None,None))

    def get_children(self): return tuple(r['id'] for r in self.rows)
    def selection(self): return (self.selected_id,) if self.selected_id else ()
    def set_rows(self,rows):
        self.cancel_edit();self.rows=rows
        if self.selected_id not in self.get_children():self.selected_id=None
        self.draw()

    def dimensions(self):
        width=max(1,self.canvas.winfo_width());base=sum(col[2] for col in self.columns)
        extra=max(0,width-base);sizes=[c[2]+(extra if c[0]=='comment' else 0) for c in self.columns]
        return width,sizes,sum(sizes)

    def clip(self,text,width,font=None):
        font=font or self.font;text=str(text).replace('\n',' ')
        if font.measure(text)<=width:return text
        lo,hi=0,len(text)
        while lo<hi:
            mid=(lo+hi+1)//2
            if font.measure(text[:mid]+'…')<=width:lo=mid
            else:hi=mid-1
        return text[:lo]+'…'

    def draw(self):
        c=self.canvas;h=self.header;c.delete('all');h.delete('all');self.hits=[]
        width,sizes,total=self.dimensions();height=max(1,c.winfo_height())
        capacity=max(1,height//self.rh)
        self.start=min(max(0,self.start),max(0,len(self.rows)-capacity))
        self.offset=min(max(0,self.offset),max(0,total-width))
        x=-self.offset
        for (key,label,_),size in zip(self.columns,sizes):
            h.create_text(x+14,self.hh/2,text=label,anchor='w',fill=INK,font=self.font)
            h.create_line(x+size,0,x+size,self.hh,fill=LINE);x+=size
        h.create_line(0,self.hh-1,max(width,total),self.hh-1,fill=LINE)
        for ri,r in enumerate(self.rows[self.start:self.start+capacity+1]):
            y=ri*self.rh;x=-self.offset
            fill='#e2f0ff' if r['id']==self.selected_id else ('#ffffff' if (ri+self.start)%2==0 else '#fcfdff')
            c.create_rectangle(0,y,max(width,total),y+self.rh,fill=fill,outline='')
            for (key,_,_),size in zip(self.columns,sizes):
                if key=='status':
                    bg,fg=STATUS_COLORS.get(r['status'],STATUS_COLORS['Создан'])
                    roundrect(c,x+10,y+9,x+size-10,y+self.rh-9,14,fill=bg,outline='')
                    c.create_oval(x+20,y+self.rh/2-4,x+28,y+self.rh/2+4,fill=fg,outline='')
                    c.create_text(x+36,y+self.rh/2,anchor='w',text=self.clip(r['status'],size-48),fill=fg,font=self.font)
                elif key=='actions':
                    for j,action in enumerate(('folder','related_folder','menu')):
                        left=x+12+j*40
                        enabled=action!='related_folder' or bool(r.get('_related_id'))
                        roundrect(c,left,y+8,left+32,y+self.rh-8,5,fill='white',outline=LINE)
                        icon(c,'calc' if action=='related_folder' else ('folder' if action=='folder' else 'more'),left+16,y+self.rh/2,INK if enabled else '#cbd3e2')
                        self.hits.append((left,y+8,left+32,y+self.rh-8,r,action,enabled))
                else:
                    value=r.get('_cells',{}).get(key,'')
                    font=self.bold if key=='num' else self.font
                    if key=='comment':
                        roundrect(c,x+10,y+7,x+size-10,y+self.rh-7,5,fill='white',outline=LINE)
                    c.create_text(x+14,y+self.rh/2,text=self.clip(value,size-28,font),anchor='w',
                        fill=BLUE if key=='related' and r.get('_related_id') else INK,font=font)
                c.create_line(x+size,y,x+size,y+self.rh,fill=LINE);x+=size
            c.create_line(0,y+self.rh,total,y+self.rh,fill=LINE)
        if not self.rows:
            c.create_text(width/2,75,text='Записей пока нет',fill=MUTED,font=('Segoe UI',15))
            c.create_text(width/2,110,text='Создайте запись или измените фильтры',fill=MUTED,font=self.font)
        count=max(1,len(self.rows));self.vbar.set(self.start/count,min(1,(self.start+capacity)/count))
        self.hbar.set(self.offset/max(total,1),min(1,(self.offset+width)/max(total,1)))
        if total<=width:self.hbar.grid_remove()
        else:self.hbar.grid()

    def scroll(self,*args):
        if self.editor:return # explicit Enter/Escape prevents loss of a draft
        if args[0]=='moveto':self.start=int(float(args[1])*len(self.rows))
        else:self.start+=int(args[1])*(max(1,self.canvas.winfo_height()//self.rh) if args[2]=='pages' else 1)
        self.draw()

    def hscroll(self,*args):
        if self.editor:return
        _,_,total=self.dimensions()
        if args[0]=='moveto':self.offset=int(float(args[1])*total)
        else:self.offset+=int(args[1])*40
        self.draw()

    def move(self,delta):
        if not self.rows:return
        ids=self.get_children();i=ids.index(self.selected_id) if self.selected_id in ids else 0
        i=max(0,min(len(ids)-1,i+delta));self.selected_id=ids[i]
        cap=max(1,self.canvas.winfo_height()//self.rh)
        if i<self.start:self.start=i
        elif i>=self.start+cap:self.start=i-cap+1
        self.draw()

    def activate(self,action):
        r=next((r for r in self.rows if r['id']==self.selected_id),None)
        if r:self.on_action(action,r,None)

    def click(self,e):
        if self.editor:
            self.editor.focus_set();return
        index=self.start+int(e.y//self.rh)
        if index>=len(self.rows):return
        r=self.rows[index];self.selected_id=r['id'];self.canvas.focus_set()
        for x1,y1,x2,y2,row,action,enabled in self.hits:
            if x1<=e.x<=x2 and y1<=e.y<=y2:
                self.draw()
                if enabled:self.on_action(action,row,e)
                return
        _,sizes,_=self.dimensions();x=-self.offset
        for (key,_,_),size in zip(self.columns,sizes):
            if x<=e.x<x+size:
                self.draw()
                if key=='comment':self.edit(r,x,(index-self.start)*self.rh,size)
                elif key=='related' and r.get('_related_id'):self.on_action('related',r,e)
                elif key=='status':self.on_action('status',r,e)
                elif key!='actions':self.on_action('card',r,e)
                return
            x+=size

    def edit_selected(self):
        if not self.selected_id:return
        index=self.get_children().index(self.selected_id);x=-self.offset
        for (key,_,_),size in zip(self.columns,self.dimensions()[1]):
            if key=='comment':self.edit(self.rows[index],x,(index-self.start)*self.rh,size);return
            x+=size

    def edit(self,r,x,y,width):
        self.cancel_edit();entry=ttk.Entry(self.canvas,font=self.font)
        self.editor=entry;entry.insert(0,r['comment']);entry.place(x=x+10,y=y+7,width=width-20,height=self.rh-14)
        entry.focus_set();entry.icursor('end')
        entry.bind('<Return>',lambda e:self.on_action('comment',r,entry.get()))
        entry.bind('<Escape>',lambda e:self.cancel_edit())

    def cancel_edit(self):
        if self.editor:self.editor.destroy();self.editor=None

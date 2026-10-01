"""Centered, monitor-aware dialogs with a fixed action area."""
import sys
import tkinter as tk
from tkinter import ttk
from .widgets import BG


def work_area(parent):
    """Work area of the monitor containing the parent, including negative origins."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),
                        ('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        user=ctypes.windll.user32
        user.MonitorFromWindow.argtypes=(wintypes.HWND,wintypes.DWORD)
        user.MonitorFromWindow.restype=wintypes.HANDLE
        user.GetMonitorInfoW.argtypes=(wintypes.HANDLE,ctypes.POINTER(MonitorInfo))
        user.GetMonitorInfoW.restype=wintypes.BOOL
        info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
        monitor=user.MonitorFromWindow(parent.winfo_id(),2)
        if user.GetMonitorInfoW(monitor,ctypes.byref(info)):
            r=info.rcWork
            return r.left,r.top,r.right,r.bottom
    return 0,0,parent.winfo_screenwidth(),parent.winfo_screenheight()


def present(window,parent,size=None):
    """Size to usable monitor bounds, then center over the main application."""
    window.update_idletasks();parent.update_idletasks()
    left,top,right,bottom=work_area(parent)
    # Reserve native title bar/borders as well as a small screen-edge margin.
    width,height=size or (window.winfo_reqwidth(),window.winfo_reqheight())
    width=min(max(300,width),max(300,right-left-40))
    height=min(max(150,height),max(150,bottom-top-80))
    x=parent.winfo_rootx()+(parent.winfo_width()-width)//2
    y=parent.winfo_rooty()+(parent.winfo_height()-height)//2-16
    x=max(left+12,min(x,right-width-20));y=max(top+12,min(y,bottom-height-45))
    # Tk negative geometry means distance from right edge, so use Win32 positioning
    # after mapping for monitors left of / above the primary display.
    window.geometry(f'{width}x{height}+{max(0,x)}+{max(0,y)}')
    window.deiconify();window.update_idletasks()
    if sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        user=ctypes.windll.user32
        user.GetParent.argtypes=(wintypes.HWND,);user.GetParent.restype=wintypes.HWND
        user.SetWindowPos.argtypes=(wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT)
        user.SetWindowPos.restype=wintypes.BOOL
        hwnd=user.GetParent(window.winfo_id())
        user.SetWindowPos(hwnd,None,x,y,0,0,0x0001|0x0004|0x0010)
    window.lift()


class Dialog(tk.Toplevel):
    def __init__(self,parent,title,scroll=False):
        super().__init__(parent)
        self.withdraw();self.title(title);self.transient(parent);self.configure(bg=BG)
        self.footer=ttk.Frame(self,padding=(16,12));self.footer.pack(side='bottom',fill='x')
        ttk.Separator(self).pack(side='bottom',fill='x')
        if scroll:
            container=ttk.Frame(self);container.pack(fill='both',expand=True)
            self.canvas=tk.Canvas(container,bg=BG,highlightthickness=0)
            scrollbar=ttk.Scrollbar(container,orient='vertical',command=self.canvas.yview)
            scrollbar.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
            self.canvas.configure(yscrollcommand=scrollbar.set)
            self.body=ttk.Frame(self.canvas,padding=16)
            item=self.canvas.create_window(0,0,window=self.body,anchor='nw')
            self.body.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
            self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(item,width=e.width))
            def wheel(e):
                if isinstance(e.widget,(tk.Text,ttk.Combobox)):return
                if self.body.winfo_height()>self.canvas.winfo_height():
                    self.canvas.yview_scroll(-int(e.delta/120),'units')
            self.bind('<MouseWheel>',wheel)
            # Ensure keyboard navigation reveals fields below the fold.
            def focus(e):
                if not str(e.widget).startswith(str(self.body)+'.'):return
                self.update_idletasks()
                y=e.widget.winfo_rooty()-self.body.winfo_rooty()
                height=max(1,self.body.winfo_height());start=self.canvas.canvasy(0)
                if y<start:self.canvas.yview_moveto(max(0,y-8)/height)
                elif y+e.widget.winfo_height()>start+self.canvas.winfo_height():
                    self.canvas.yview_moveto(max(0,y+e.widget.winfo_height()-self.canvas.winfo_height()+8)/height)
            self.bind('<FocusIn>',focus)
        else:
            self.body=ttk.Frame(self,padding=16);self.body.pack(fill='both',expand=True)
        self.bind('<Escape>',lambda e:self.destroy())

    def show(self,size=None):
        present(self,self.master,size)
        return self

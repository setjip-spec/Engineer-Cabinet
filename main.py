import argparse


def main():
    parser=argparse.ArgumentParser(description='Личный кабинет инженера')
    parser.add_argument('--restore',nargs='?',const='',help='Восстановить копию в новую пустую папку')
    parser.add_argument('--root',help='Общая папка базы и файлов')
    parser.add_argument('--choose-root',action='store_true',help='Выбрать другую базу')
    parser.add_argument('--smoke-test',action='store_true',help='Проверить запуск на временной базе')
    args=parser.parse_args()
    if args.restore is not None:
        import tkinter as tk
        from tkinter import filedialog,messagebox
        from engineer_cabinet.core import restore_backup
        w=tk.Tk();w.withdraw()
        archive=args.restore or filedialog.askopenfilename(title='Резервная копия',filetypes=[('ZIP','*.zip')])
        dest=args.root or (filedialog.askdirectory(title='Новая пустая папка восстановления') if archive else '')
        if archive and dest:
            try:
                restore_backup(archive,dest)
                messagebox.showinfo('Восстановлено','База и файлы восстановлены: '+dest+'\nТеперь откройте эту папку через --choose-root.')
            except Exception as e:messagebox.showerror('Не восстановлено',str(e))
        w.destroy()
    elif args.smoke_test:
        import tempfile
        import tkinter as tk
        from engineer_cabinet.core import Cabinet,MANAGERS
        from engineer_cabinet.ui import App
        with tempfile.TemporaryDirectory() as tmp:
            c=Cabinet(tmp)
            c.create('quote',MANAGERS[0])
            w=tk.Tk();app=App(w,c);w.after(1800,app.quit);w.mainloop()
    else:
        from engineer_cabinet.ui import launch
        launch(args.root,args.choose_root)


if __name__=='__main__':
    main()

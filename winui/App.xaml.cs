using Microsoft.UI.Xaml;
using System;
using System.IO;

namespace winui
{
    /// <summary>
    /// 应用外壳：建主窗口并激活。
    /// 页面构造函数、异步续体里抛出来的异常默认会让进程直接没了，本机跑又没有任何记录，
    /// 所以这里兜一道，把出错的现场写进 %LocalAppData%\invoice_management_system\crash.log。
    /// </summary>
    public partial class App : Application
    {
        /// <summary>
        /// 页面里要用窗口句柄（文件夹选择器需要绑到当前窗口），这里留一个入口。
        /// </summary>
        public static Window? MainWindow { get; private set; }

        public App()
        {
            InitializeComponent();
            UnhandledException += (sender, args) =>
            {
                LogCrash(args.Exception);
                args.Handled = true;      // 报出去之后让窗口继续活着，别整个退掉
            };
        }

        protected override void OnLaunched(Microsoft.UI.Xaml.LaunchActivatedEventArgs args)
        {
            MainWindow = new MainWindow();
            MainWindow.Activate();
        }

        private static void LogCrash(Exception exception)
        {
            try
            {
                var root = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "invoice_management_system");
                Directory.CreateDirectory(root);
                File.AppendAllText(Path.Combine(root, "crash.log"),
                    $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {exception.GetType().Name}: {exception.Message}\n{exception.StackTrace}\n\n");
            }
            catch (Exception)
            {
                // 连日志都写不了就只能算了，不能让记录崩溃本身再崩一次
            }
        }
    }
}

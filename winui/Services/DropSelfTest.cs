using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace winui.Services
{
    /// <summary>
    /// 诊断用：把「拖放接收这套接线在运行时到底是个什么状态」逐条追加进一个文件，让脚本能断言。
    ///
    /// 为什么只剩这一条可验证的路：真鼠标落下在本机自动化不了——注入鼠标的脚本一落地就被杀软清掉；
    /// 从进程外把 Drop 递给放置目标也不行（`GetProp` 回的接口指针只在属主进程地址空间有效，
    /// 进程外解引用是 CLR 致命错误）；在进程内自己去查也不行——把本进程 7 个顶层窗口连子窗口
    /// 共 10 个 HWND 全扫过，`OleDropTargetInterface`／`OleDropTarget` 两个属性都是空的，
    /// 也就是 WinUI 3 的 OLE 接线不通过这条公开渠道暴露，微软那一段我们递不进去。
    /// 于是能诚实验证的只剩**我们自己那一段**：`AllowDrop` 这两个开关此刻是不是开的、
    /// 扫描期间关掉之后有没有恢复（历史上就出过「切页复用缓存页 → AllowDrop 停在 false →
    /// 看着像拖不进去」这种回归），以及落点是否真落在这个进程的窗口上。
    ///
    /// 只在设了环境变量 INVOICE_DROP_SELFTEST 时执行，正常运行是死路；输出追加到 ..._OUT 的文件，
    /// 由 temp\drop_transport_check.ps1 读取并断言。
    /// </summary>
    public static class DropSelfTest
    {
        private const string EnvKey = "INVOICE_DROP_SELFTEST";
        private const string EnvOut = "INVOICE_DROP_SELFTEST_OUT";

        [StructLayout(LayoutKind.Sequential)]
        private struct POINTL
        {
            public int x;
            public int y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int x;
            public int y;
        }

        [DllImport("user32.dll")]
        private static extern IntPtr GetActiveWindow();

        [DllImport("user32.dll")]
        private static extern uint GetDpiForWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ClientToScreen(IntPtr hWnd, ref POINT lpPoint);

        [DllImport("user32.dll")]
        private static extern IntPtr WindowFromPoint(POINTL pt);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentProcessId();

        /// <summary>页面 Loaded 时调用一次；除了报当前状态，还顺带把落点归属测一遍。</summary>
        public static void RunIfRequested(FrameworkElement folderBox, Border dropZone, Button pasteButton, int droppedCount)
        {
            if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(EnvKey)))
            {
                return;
            }

            _ = ReportAsync(folderBox, dropZone, pasteButton, droppedCount);
        }

        /// <summary>
        /// 记一行「此刻这套接线是什么样」。扫描状态每次切换都调一下，
        /// 脚本就能验「扫的时候关掉、扫完恢复」这条恢复路径——它坏了的表象和用户手拖失败一模一样。
        /// </summary>
        public static void Snapshot(string reason, Border? dropZone, FrameworkElement? folderBox, Button? pasteButton, int droppedCount)
        {
            if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(EnvKey)))
            {
                return;
            }

            var line = $"[{reason}] DropZone.AllowDrop={(dropZone?.AllowDrop == true)} " +
                       $"FolderPathBox.AllowDrop={(folderBox?.AllowDrop == true)} " +
                       $"PasteButton.IsEnabled={(pasteButton?.IsEnabled == true)} 清单={droppedCount}";
            Append(line);
        }

        private static async Task ReportAsync(FrameworkElement folderBox, Border dropZone, Button pasteButton, int droppedCount)
        {
            try
            {
                await Task.Delay(1200);      // 等控件真的长出来，别跑在布局之前
                Snapshot("启动时", dropZone, folderBox, pasteButton, droppedCount);

                // 落点几何：源目录框的中心换算成物理屏幕坐标，再问系统「这个点上是哪个窗口」。
                // 归属本进程 = 用户把手放在这个框上松手时，拖拽确实会交给我们这个窗口
                var hwnd = GetActiveWindow();
                var scale = GetDpiForWindow(hwnd) / 96.0;
                var p = folderBox.TransformToVisual(null).TransformPoint(
                    new Windows.Foundation.Point(folderBox.ActualWidth / 2, folderBox.ActualHeight / 2));
                var client = new POINT { x = (int)Math.Round(p.X * scale), y = (int)Math.Round(p.Y * scale) };
                _ = ClientToScreen(hwnd, ref client);
                var under = WindowFromPoint(new POINTL { x = client.x, y = client.y });
                _ = GetWindowThreadProcessId(under, out var pidUnder);
                Append($"落点({client.x},{client.y}) 命中窗口 pid={pidUnder}，本进程 pid={GetCurrentProcessId()}，落点归属本进程={pidUnder == GetCurrentProcessId()}");
            }
            catch (Exception ex)
            {
                Append($"EXCEPTION {ex.GetType().Name} {ex.Message}");
            }
        }

        private static void Append(string line)
        {
            var path = Environment.GetEnvironmentVariable(EnvOut);
            if (string.IsNullOrWhiteSpace(path))
            {
                return;
            }

            try
            {
                File.AppendAllText(path, line + Environment.NewLine);
            }
            catch (Exception)
            {
                // 诊断产物写不出去就算了，不能因为它把页面带崩
            }
        }
    }
}

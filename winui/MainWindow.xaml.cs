using System;
using System.Linq;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using winui.Services;

// To learn more about WinUI, the WinUI project structure,
// and more about our project templates, see: http://aka.ms/winui-project-info.

namespace winui
{
    /// <summary>
    /// 应用外壳：左侧 NavigationView 四个页面，内容落在 Frame 里。
    /// </summary>
    public sealed partial class MainWindow : Window
    {
        private static readonly string[] PageTags = { "Home", "Data", "Invoices", "Settings" };

        public MainWindow()
        {
            InitializeComponent();
            // 返回按钮是关掉的（IsBackButtonVisible=Collapsed），后退栈和 Frame 默认那 10 页缓存
            // 在这个应用里没有任何入口会用，白攥着离开过的页面实例
            if (ContentFrame != null)
            {
                ContentFrame.CacheSize = 1;
            }

            RestoreLastPage();
        }

        private void RootNavigation_ItemInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
        {
            if (args.InvokedItemContainer?.Tag is not string tag)
            {
                return;
            }

            NavigateTo(tag);
            LocalStore.Write(LocalStore.LastPageKey, tag);
        }

        private void ContentFrame_Navigated(object sender, NavigationEventArgs e)
        {
            // 后退栈没有入口可达（返回按钮关着），每次导航完直接清掉，别让离开的页面一直被记着
            if (ContentFrame != null)
            {
                ContentFrame.BackStack.Clear();
            }

            // 恢复上次页面时是代码直接 Navigate，不走 ItemInvoked，高亮得自己对上
            if (RootNavigation == null || e.SourcePageType == null)
            {
                return;
            }

            foreach (var item in RootNavigation.MenuItems.OfType<NavigationViewItem>())
            {
                if (item.Tag is string tag && PageType(tag) == e.SourcePageType)
                {
                    RootNavigation.SelectedItem = item;
                    return;
                }
            }
        }

        private void RestoreLastPage()
        {
            var tag = LocalStore.ReadBool(LocalStore.RestoreLastPageKey, true)
                ? LocalStore.Read(LocalStore.LastPageKey, "Home")
                : "Home";
            if (!PageTags.Contains(tag))
            {
                tag = "Home";
            }

            try
            {
                NavigateTo(tag);
            }
            catch (Exception)
            {
                // 上次停在哪个页面要是把构造函数炸了，不退回首页就永远开不起来，
                // 只能靠手工删 %LocalAppData%\invoice_management_system\last_page.txt
                if (tag != "Home")
                {
                    LocalStore.Write(LocalStore.LastPageKey, "Home");
                    NavigateTo("Home");
                }
            }
        }

        private void NavigateTo(string tag)
        {
            var type = PageType(tag);
            if (ContentFrame == null || type == null || ContentFrame.CurrentSourcePageType == type)
            {
                return;
            }

            ContentFrame.Navigate(type);
        }

        private static Type? PageType(string tag) => tag switch
        {
            "Home" => typeof(Pages.HomePage),
            "Data" => typeof(Pages.DataPage),
            "Invoices" => typeof(Pages.InvoicePage),
            "Settings" => typeof(Pages.SettingsPage),
            _ => null
        };
    }
}

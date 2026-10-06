using Microsoft.UI.Xaml.Controls;
using winui.Services;

namespace winui.Pages
{
    /// <summary>首页：概览与待办占位，数据接入后填这几张卡片。</summary>
    public sealed partial class HomePage : Page
    {
        public HomePage()
        {
            this.InitializeComponent();
            // 连接是扫描时才建立的，这里只报当前配的服务地址，不假装自己探测过
            if (ServiceStateText != null)
            {
                ServiceStateText.Text = ApiClient.Current.BaseUrl;
            }
        }
    }
}

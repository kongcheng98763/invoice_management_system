using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using winui.Services;

namespace winui.Pages
{
    /// <summary>设置：本地服务地址、归档目录显示路径和界面偏好，全部落成单行文本。</summary>
    public sealed partial class SettingsPage : Page
    {
        public SettingsPage()
        {
            this.InitializeComponent();
            LoadValues();
        }

        private void LoadValues()
        {
            if (ApiBaseUrlBox == null || WsUrlBox == null || ArchiveDirBox == null || RestoreLastPageSwitch == null)
            {
                return;
            }

            ApiBaseUrlBox.Text = ApiClient.Current.BaseUrl;
            WsUrlBox.Text = WsClient.Current.Url;
            ArchiveDirBox.Text = LocalStore.Read(LocalStore.ArchiveDirKey, string.Empty);
            RestoreLastPageSwitch.IsOn = LocalStore.ReadBool(LocalStore.RestoreLastPageKey, true);
        }

        private void SaveButton_Click(object sender, RoutedEventArgs e)
        {
            if (ApiBaseUrlBox == null || WsUrlBox == null || ArchiveDirBox == null || RestoreLastPageSwitch == null)
            {
                return;
            }

            var api = ApiBaseUrlBox.Text.Trim();
            var ws = WsUrlBox.Text.Trim();
            var archive = ArchiveDirBox.Text.Trim();

            // 留空不是「改成空地址」，是还没填：不保存也不说「已保存」，否则用户以为改过了
            var skipped = new List<string>();
            var failed = new List<string>();

            if (api.Length > 0)
            {
                if (LocalStore.Write(LocalStore.ApiBaseUrlKey, api))
                {
                    ApiClient.Current.BaseUrl = api;
                }
                else
                {
                    failed.Add("服务地址");
                }
            }
            else
            {
                skipped.Add("服务地址");
            }

            if (ws.Length > 0)
            {
                if (LocalStore.Write(LocalStore.WsUrlKey, ws))
                {
                    WsClient.Current.Url = ws;
                }
                else
                {
                    failed.Add("推送地址");
                }
            }
            else
            {
                skipped.Add("推送地址");
            }

            if (!LocalStore.Write(LocalStore.ArchiveDirKey, archive) ||
                !LocalStore.Write(LocalStore.RestoreLastPageKey, RestoreLastPageSwitch.IsOn))
            {
                failed.Add("归档目录/偏好");
            }

            if (SaveStatusText != null)
            {
                SaveStatusText.Text = StatusOf(failed, skipped);
            }
        }

        private static string StatusOf(List<string> failed, List<string> skipped)
        {
            if (failed.Count > 0)
            {
                return $"没写进本地文件：{string.Join("、", failed)}（磁盘满了或目录没权限）";
            }

            return skipped.Count > 0 ? $"已保存，但空着的没改：{string.Join("、", skipped)}" : "已保存";
        }

        private async void TestConnectionButton_Click(object sender, RoutedEventArgs e)
        {
            if (ConnectionStatusText == null)
            {
                return;
            }

            ConnectionStatusText.Text = "检测中…";
            try
            {
                // 客户端超时是 10 分钟（扫描要跑那么久），检测不能用它，否则地址写得通但不响应时按钮挂死
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                var text = await ApiClient.Current.GetRawAsync(ApiClient.HealthPath, timeout.Token);
                ConnectionStatusText.Text = $"服务在线：{Brief(text)}";
            }
            catch (HttpRequestException ex)
            {
                ConnectionStatusText.Text = $"本地服务不可用：{ex.Message}";
            }
            catch (TaskCanceledException)
            {
                ConnectionStatusText.Text = "本地服务无响应（5 秒超时）";
            }
            catch (Exception ex)
            {
                ConnectionStatusText.Text = $"地址无效或检测失败：{ex.Message}";
            }
        }

        private async void ConnectWebSocketButton_Click(object sender, RoutedEventArgs e)
        {
            if (ConnectionStatusText == null)
            {
                return;
            }

            if (WsUrlBox != null)
            {
                var ws = WsUrlBox.Text.Trim();
                if (ws.Length > 0)
                {
                    WsClient.Current.Url = ws;
                }
            }

            ConnectionStatusText.Text = "连接中…";
            try
            {
                await WsClient.Current.ConnectAsync();
                ConnectionStatusText.Text = $"推送已连接：{WsClient.Current.Url}";
            }
            catch (Exception ex)
            {
                ConnectionStatusText.Text = $"推送连不上：{ex.Message}";
            }
        }

        private static string Brief(string text)
        {
            var oneLine = text.Replace("\r", " ").Replace("\n", " ").Trim();
            return oneLine.Length <= 120 ? oneLine : oneLine.Substring(0, 120) + "…";
        }
    }
}

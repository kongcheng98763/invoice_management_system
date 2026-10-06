using System;
using System.Collections.ObjectModel;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using winui.Models;
using winui.Services;

namespace winui.Pages
{
    /// <summary>报销台账：与报销总述等价的一张表，数据全部来自本地服务。</summary>
    public sealed partial class DataPage : Page
    {
        private readonly ObservableCollection<ReimbursementRow> _rows = new ObservableCollection<ReimbursementRow>();

        public DataPage()
        {
            this.InitializeComponent();
            if (LedgerList != null)
            {
                LedgerList.ItemsSource = _rows;
            }
        }

        private async void RefreshButton_Click(object sender, RoutedEventArgs e)
        {
            if (LedgerStatusText == null)
            {
                return;
            }

            LedgerStatusText.Text = "读取中…";
            try
            {
                // 台账端点还没落地（need.md 五的 SQLite 没建），服务侧压根没有 GET /reimbursements；
                // 分页与筛选参数等它定稿后再接
                var rows = await ApiClient.Current.GetAsync<ReimbursementRow[]>(ApiClient.ReimbursementsPath);
                _rows.Clear();
                if (rows != null)
                {
                    foreach (var row in rows)
                    {
                        _rows.Add(row);
                    }
                }
                LedgerStatusText.Text = _rows.Count == 0 ? "台账为空" : $"共 {_rows.Count} 行";
            }
            catch (ApiStatusException ex)
            {
                // 台账端点还没落地，404/501 不能报成「服务不可用」——服务明明是活的
                LedgerStatusText.Text = ex.StatusCode == HttpStatusCode.NotFound || ex.StatusCode == HttpStatusCode.NotImplemented
                    ? $"台账还没有后端（{(int)ex.StatusCode} {ex.StatusCode}），等 Python 侧实现 {ApiClient.ReimbursementsPath} 后才有数据"
                    : $"服务返回错误：{ex.Message}";
            }
            catch (HttpRequestException ex)
            {
                LedgerStatusText.Text = $"本地服务不可用：{ex.Message}";
            }
            catch (TaskCanceledException)
            {
                LedgerStatusText.Text = "本地服务无响应（超时）";
            }
            catch (JsonException ex)
            {
                // 服务侧结构还在变，返回体对不上时只提示不改界面状态
                LedgerStatusText.Text = $"返回结构不符：{ex.Message}";
            }
            catch (Exception ex)
            {
                LedgerStatusText.Text = $"读取失败：{ex.Message}";
            }
        }
    }
}

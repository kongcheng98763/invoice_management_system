using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Microsoft.Windows.Storage.Pickers;
using winui.Models;
using winui.Services;

namespace winui.Pages
{
    /// <summary>发票识别：选目录 → 让本地服务扫描归档 → 看实时日志与四类结果。</summary>
    public sealed partial class InvoicePage : Page
    {
        private static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions(JsonSerializerDefaults.Web);

        // 扫描是进程级的一件事（服务那边也只有一个扫描线程），字段必须也是静态的：
        // 切页会重建实例，实例字段会让新页以为「没在扫」从而再发一轮
        private static bool _scanning;

        // 广播给所有活着的页面实例，各自更新自己那套控件
        private static event Action<bool>? ScanStateChanged;

        // 每轮扫描一个代号：同一份结果在一个实例里只渲染一次
        // （POST 响应和 WebSocket 的 done 推送都会带回同一份，先到的那个算数）
        private static int _scanGeneration;
        private int _renderedFor = -1;

        // 最近一次扫描结果的一份进程级副本：切页会重建实例（CacheSize=1 只留最近那一页，
        // 发票识别页会被挤掉），新实例的卡片是空的，日志却还写着「已归档 N 个」，两边对不上。
        private static ClassifyReport? _lastReport;

        // 服务串行跑扫描，done 推送按点击顺序到；这两个计数用来认出「补到的上一轮结果」
        private static int _dispatchedScans;
        private static int _donePushes;
        private static string? _lastPushPayload;

        // 扫描日志的进程级副本：中途切到别的页会重建一个 InvoicePage，本页控件里的行就没了，
        // 新页在构造函数里把它倒回去，用户回来才看得到刚才跑到哪一行
        private static readonly List<string> LiveLog = new List<string>();

        private const int LiveLogCap = 500;

        // 从微信/资源管理器拖进来的 PDF；空的时候按源目录整目录扫
        // 拖入清单是进程级的，理由和 _scanning / LiveLog 一样：切页会重建一个发票识别页，
        // 实例字段会让清单跟着旧页一起没了，而静态日志还倒回「已拖入 N 个」——
        // 用户照着那条日志点「开始分类」，扫的其实是源目录而不是那 N 张
        private static readonly List<string> _droppedFiles = new List<string>();

        // 最近一次悬停在放置框上的数据带了哪些格式；读不出路径时把它写进日志，好判断该不该扩格式
        private IReadOnlyList<string> _dragFormats = Array.Empty<string>();

        // DragEnter 问一次、DragOver 每帧复用；null 表示还没收到 Enter
        private bool? _dragAcceptable;

        // 同一次拖拽里「问不到来源能给什么」只报一遍日志（DragOver 每秒十几次）
        private bool _dragProbeWarned;

        public InvoicePage()
        {
            this.InitializeComponent();
            FillCards(new List<InvoiceFileEntry>(), null);
            // 切页回来重建的这一页：把上一轮的结果补渲染出来，卡片不停在「（0）」
            if (_lastReport != null)
            {
                _renderedFor = _scanGeneration;
                Render(_lastReport);
            }

            if (ArchivePathText != null)
            {
                ArchivePathText.Text = $"归档目录：{ArchiveDir()}";   // 有扫描结果后会换成服务实际写入的那个
            }

            // 订阅挂在 Loaded 而不是构造函数：外壳那个 Frame 开着页缓存（MainWindow 里 CacheSize=1），
            // 被缓存住的那一页再回来时构造函数不会再跑一遍，只在 ctor 里订阅的话这一页就再也收不到推送
            // 和扫描状态了——按钮停在「分类中…」、放置框的 AllowDrop 停在 false，看着就是「拖不进去」（评审 P2）
            this.Loaded += InvoicePage_Loaded;
            ApplyScanState(_scanning);
            // 清单是进程级的：切页重建出来的这一页要把「已经拖进来几个」重新说一遍，
            // 不然控件是空的、静态日志却还挂着「已拖入 N 个」，点开始分类扫的是源目录
            if (_droppedFiles.Count > 0)
            {
                SetDropHint($"累计 {_droppedFiles.Count} 个 PDF 待处理（切页回来清单还在），点「开始分类」处理");
                if (ClearDropButton != null)
                {
                    ClearDropButton.Visibility = Visibility.Visible;
                }
            }
            this.Unloaded += InvoicePage_Unloaded;
            // 拖入的清单会盖过源目录（服务侧以 files 为准），所以用户一改目录就把它清掉，
            // 否则拖过一次之后再换文件夹扫描，扫的还是上次那批文件
            if (FolderPathBox != null)
            {
                // 上次扫过的目录回填，免得每次开都要重选一遍；赋值要在挂 TextChanged 之前，
                // 不然这条回填会被当成「用户改了目录」而清掉同一次拖进来的清单
                var last = LocalStore.Read(LocalStore.SourceFolderKey, string.Empty);
                if (last.Length > 0 && Directory.Exists(last))
                {
                    FolderPathBox.Text = last;
                }

                FolderPathBox.TextChanged += FolderPathBox_TextChanged;
            }
            // 实时日志靠 WebSocket 推，只有设置页点过「测试连接」才会有连接，所以进页面就先自己连一次
            _ = EnsureConnectedAsync();
            ReplayLog();
            if (LiveLog.Count == 0)
            {
                AppendLog("未开始：选择文件夹后点「开始分类」。");
                if (IsElevated())
                {
                    // 管理员权限运行的程序收不到普通权限程序（资源管理器、微信）发来的拖放，
                    // 系统直接挡掉且没有任何提示——不写出来的话就是「拖了没反应」一团谜
                    AppendLog("注意：界面正以管理员权限运行，从资源管理器或微信拖文件进来会被系统挡住，请用普通权限启动。");
                }
            }
        }

        /// <summary>推送连不上不算错误：服务检测不到订阅者时会把日志放进 POST 响应一起返回。</summary>
        private async Task EnsureConnectedAsync()
        {
            if (WsClient.Current.IsConnected)
            {
                return;
            }

            try
            {
                await WsClient.Current.ConnectAsync();
            }
            catch (Exception ex)
            {
                AppendLog($"推送未连接，日志将随响应返回：{ex.Message}");
            }
        }

        // 这一页当前有没有挂在进程级的事件上；Loaded/Unloaded 会成对反复触发，别挂第二遍
        private bool _wired;

        // 推送钩子是不是已经挂上了：全进程只挂一次，摘的动作没有（理由见 InvoicePage_Loaded）
        private static bool _pushPumpWired;

        // 现在活着的那一页：拖放的结果要落到**看得见**的那一页上，不是落到发起这次拖放的那一页上
        private static InvoicePage? _active;

        private void InvoicePage_Loaded(object sender, RoutedEventArgs e)
        {
            _active = this;
            if (_wired)
            {
                return;
            }

            _wired = true;
            // 推送钩子挂上就不摘（评审 P1）：原先在 Unloaded 里退订，扫描中途切到别的页时订阅者归零，
            // 那一段实时日志是在源头丢的、不是没渲染 —— 服务只在「一个订阅者都没有」时才把日志塞进
            // POST 响应兜底，而那一轮早就发出去了，回来也补不到。
            // 不摘不会让同一页多收：每页只挂一次，处理体按「活着的那一页」投递（见 OnServiceMessage）。
            if (!_pushPumpWired)
            {
                _pushPumpWired = true;
                WsClient.Current.MessageReceived += OnServiceMessage;
            }

            ScanStateChanged += ApplyScanState;
            ApplyScanState(_scanning);
            // 诊断通道：只有设了 INVOICE_DROP_SELFTEST 才会真的动，正常运行到这里就返回
            DropSelfTest.RunIfRequested(FolderPathBox, DropZone, PasteButton, _droppedFiles.Count);
        }

        private void InvoicePage_Unloaded(object sender, RoutedEventArgs e)
        {
            if (ReferenceEquals(_active, this))
            {
                _active = null;
            }

            _wired = false;
            // 这里只摘扫描状态；WsClient 那个钩子故意不摘，见 InvoicePage_Loaded 的说明
            ScanStateChanged -= ApplyScanState;
        }

        /// <summary>扫描期间把「开始分类」和放置框一起按住；每个实例只管自己那套控件。</summary>
        private void ApplyScanState(bool scanning)
        {
            if (StartScanButton != null)
            {
                StartScanButton.IsEnabled = !scanning;
                StartScanButton.Content = scanning ? "分类中…" : "开始分类";
            }

            if (DropZone != null)
            {
                // 扫描中途再拖一批进来会让人以为改的是这一次，先把放置框关掉
                DropZone.AllowDrop = !scanning;
            }

            if (FolderPathBox != null)
            {
                FolderPathBox.AllowDrop = !scanning;
            }

            // 「粘贴文件」读的是同一份进程级清单，扫描中途粘一下会把清单改掉——
            // 而这一轮已经发出去了，界面上写的数字和实际归档的那批就对不上（评审 P2）
            if (PasteButton != null)
            {
                PasteButton.IsEnabled = !scanning;
            }

            // 快照要放在**所有开关都改完之后**：放在前面记的是上一刻的状态，
            // `scanning=True` 那几行会永远写着 Paste=True，等于这条断言从没真验过（评审 P3）
            DropSelfTest.Snapshot($"扫描状态切换 scanning={scanning}", DropZone, FolderPathBox, PasteButton, _droppedFiles.Count);
        }

        private void OnServiceMessage(string payload)
        {
            // 钩子只挂一次、不摘，所以收到消息的未必是看得见的那一页：按「现在活着的那一页」投递（评审 P1）。
            // 没有活页（人整个不在发票识别页上）时落到自己手上，日志照样进进程级副本 LiveLog，
            // 回到这一页 ReplayLog 会把它倒出来——这一段不再是凭空消失的。
            var live = _active;
            if (live != null && !ReferenceEquals(live, this))
            {
                live.OnServiceMessage(payload);
                return;
            }

            var queue = this.DispatcherQueue;
            if (queue == null)
            {
                return;
            }

            queue.TryEnqueue(() => ApplyPush(payload));
        }

        /// <summary>在 UI 线程上认领一条推送。切页留下的旧实例也挂着同一个事件，同一条推送会被每个实例各处理一遍：静态日志副本会记双份，done 的序号也会数错，所以先认人再认序号。</summary>
        private void ApplyPush(string payload)
        {
            // 排队这几毫秒里可能正好切了页，出队时再认一次当前活着的那一页并转过去
            var live = _active;
            if (live != null && !ReferenceEquals(live, this))
            {
                live.ApplyPush(payload);
                return;
            }

            // 按同一次回调的对象标识只认一次
            if (ReferenceEquals(payload, _lastPushPayload))
            {
                return;
            }

            _lastPushPayload = payload;

            if (!payload.TrimStart().StartsWith("{", StringComparison.Ordinal))
            {
                AppendLog(payload);
                return;
            }

            try
            {
                // 与 invoice_service.py 的 /ws 推送信封一致：{"kind":"log","line":…} / {"kind":"done","report":…}
                var push = JsonSerializer.Deserialize<WsPush>(payload, JsonOptions);
                if (push == null)
                {
                    return;
                }

                if (string.Equals(push.Kind, "log", StringComparison.Ordinal) && push.Line != null)
                {
                    AppendLog(push.Line);
                }
                else if (push.Report != null)
                {
                    // 服务是串行跑的，done 推送按点击顺序到达。到的序号比点击次数小，
                    // 说明这是上一轮（响应早超时了）的结果：只补一行日志，不去占最新一轮的渲染槽，
                    // 否则界面显示的是上一批的数字，真正这一批的结果反倒被判「已渲染」。
                    if (++_donePushes < _dispatchedScans)
                    {
                        var archived = 0;
                        if (push.Report.Counts != null)
                        {
                            foreach (var value in push.Report.Counts.Values)
                            {
                                archived += value;
                            }
                        }

                        AppendLog($"补到上一轮的结果（已被更新的一次点击取代）：归档 {archived} 个");
                        return;
                    }

                    // 扫描中途切走过来的新实例没渲染过这一轮，done 推送就是它唯一能看到结果的机会
                    RenderOnce(push.Report);
                }
            }
            catch (JsonException ex)
            {
                AppendLog($"推送解析失败：{ex.Message}");
            }
        }

        private async void PickFolderButton_Click(object sender, RoutedEventArgs e)
        {
            var window = App.MainWindow;
            if (window == null || FolderPathBox == null)
            {
                return;
            }

            try
            {
                // 选择器要绑到宿主窗口，否则对话框不置顶
                var picker = new FolderPicker(window.AppWindow.Id);
                var result = await picker.PickSingleFolderAsync();
                if (result != null)
                {
                    FolderPathBox.Text = result.Path;
                }
            }
            catch (Exception ex)
            {
                // 选择器起不来时不拦主流程，路径本来也能手填
                AppendLog($"文件夹选择器不可用：{ex.Message}");
            }
        }

        private async void StartScanButton_Click(object sender, RoutedEventArgs e)
        {
            if (_scanning)
            {
                // 切页重建的实例也一样看不到「正在扫」，所以这里挡的是进程级状态
                AppendLog("上一次扫描还没结束，这一轮先不重复发起");
                return;
            }

            if (FolderPathBox == null || StartScanButton == null)
            {
                return;
            }

            var folder = FolderPathBox.Text.Trim().Trim('"');
            // 清单要和目录**同一时刻**取：下面有两次 await（连推送），这期间源目录框还打得字，
            // 一敲键盘 TextChanged 就把清单清了，于是发出去的是「旧目录 + 空清单」——
            // 用户刚拖进来的那几张凭空消失，扫的却是上一个文件夹（评审 P2）
            var files = new List<string>(_droppedFiles);
            if (files.Count == 0 && (folder.Length == 0 || !Directory.Exists(folder)))
            {
                AppendLog("请先选择存在的文件夹，或把 PDF 直接拖进上面的框");
                return;
            }

            _scanning = true;
            // 新一轮从头记日志，切页期间留下的旧记录不该混进来
            LiveLog.Clear();
            _displayLines.Clear();
            _scanGeneration++;
            _dispatchedScans++;
            var timedOut = false;
            try
            {
                ScanStateChanged?.Invoke(true);
                if (folder.Length > 0)
                {
                    LocalStore.Write(LocalStore.SourceFolderKey, folder);
                }
                AppendLog(files.Count > 0 ? $"开始处理拖入的 {files.Count} 个文件" : $"开始扫描：{folder}");
                // 扫描期间日志逐行推过来，所以发请求前确保连接在；但推送只是日志通道，
                // 一条半死的 socket 不该把「开始分类」卡住，给它五秒就走
                var connect = EnsureConnectedAsync();
                if (await Task.WhenAny(connect, Task.Delay(TimeSpan.FromSeconds(5))) != connect)
                {
                    AppendLog("推送连接超时，本次扫描的日志改随响应返回");
                }

                var report = await ApiClient.Current.PostAsync<ClassifyReport>(
                    ApiClient.ClassifyPath, new { folder, files });
                if (report == null)
                {
                    AppendLog("服务没有返回结果");
                }
                else
                {
                    if (report.Log != null)
                    {
                        foreach (var line in report.Log)
                        {
                            AppendLog(line);
                        }
                    }

                    RenderOnce(report);
                }
            }
            catch (ApiStatusException ex)
            {
                AppendLog($"服务拒绝了这次扫描：{ex.Message}");
            }
            catch (HttpRequestException ex)
            {
                AppendLog($"本地服务不可用：{ex.Message}");
            }
            catch (TaskCanceledException)
            {
                // 请求超时只是这条 HTTP 连接断了，服务侧的扫描还在跑，
                // 跑完会通过推送补上结果——这时候直接报"失败"会让人重复提交同一批票
                timedOut = true;
                AppendLog("等响应超时了，但服务可能还在后台跑：先看下面的日志或归档目录，别急着再点一次");
            }
            catch (JsonException ex)
            {
                AppendLog($"返回结构不符：{ex.Message}");
            }
            finally
            {
                // 除了超时，其它走完的路径都说明这一轮已经落定（服务端不会再有推送欠着），
                // 把推送序号对齐，否则下一轮的推送会被误判成「上一轮补到的」
                if (!timedOut)
                {
                    _donePushes = _dispatchedScans;
                }

                _scanning = false;
                ScanStateChanged?.Invoke(false);
            }
        }

        // 服务实际写入的归档目录：本机设置里那个只是显示用，两边不一致时以这里为准，
        // 否则刚拖完点「打开归档目录」会打开一个空文件夹，看起来像没收到文件
        private static string? _lastOutDir;

        private void OpenArchiveButton_Click(object sender, RoutedEventArgs e)
        {
            var dir = _lastOutDir ?? ArchiveDir();
            if (!Directory.Exists(dir))
            {
                AppendLog($"归档目录还不存在：{dir}");
                return;
            }

            try
            {
                Process.Start(new ProcessStartInfo("explorer.exe", $"\"{dir}\"") { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                AppendLog($"打不开归档目录：{ex.Message}");
            }
        }

        private void Render(ClassifyReport report)
        {
            var files = report.Files ?? new List<InvoiceFileEntry>();
            FillCards(files, report.Counts);

            if (MultiPageNoteText != null)
            {
                MultiPageNoteText.Text = $"多页文件 {report.MultiPage} 个：按第一页判类，归入所属类别下的「多页文件」子文件夹。";
            }

            if (!string.IsNullOrWhiteSpace(report.OutDir))
            {
                _lastOutDir = report.OutDir;
                if (ArchivePathText != null)
                {
                    ArchivePathText.Text = $"归档目录：{_lastOutDir}";
                }
            }

            // 票面字段跟着各自的条目走，单独列一串就对不上是哪个文件的票
            foreach (var entry in files)
            {
                if (entry.Fields == null)
                {
                    continue;
                }

                var described = Describe(entry.Fields);
                if (described.Length > 0)
                {
                    AppendLog($"  {entry.Display}  {described}");
                }
            }

            // 告警在服务侧是攒起来一次性回传的，超过 MAX_WARNINGS 时只给前若干条
            if (report.WarningCount > 0)
            {
                AppendLog($"解析告警 {report.WarningCount} 条：");
                foreach (var warning in report.Warnings ?? new List<string>())
                {
                    AppendLog("  ! " + warning);
                }

                if (report.WarningCount > (report.Warnings?.Count ?? 0))
                {
                    AppendLog("  …其余告警见本地服务控制台");
                }
            }
        }

        private void RenderOnce(ClassifyReport report)
        {
            if (_renderedFor == _scanGeneration)
            {
                AppendLog("扫描完成。");
                return;
            }

            _renderedFor = _scanGeneration;
            _lastReport = report;   // 给切页后重建的实例留一份可补渲染的结果
            Render(report);
            AppendLog(SummaryOf(report));
        }

        private void FillCards(IReadOnlyList<InvoiceFileEntry> files, IDictionary<string, int>? counts)
        {
            FillCard(TrainHeaderText, TrainList, InvoiceCategories.Train, files, counts);
            FillCard(VatHeaderText, VatList, InvoiceCategories.Vat, files, counts);
            FillCard(RegularHeaderText, RegularList, InvoiceCategories.Regular, files, counts);
            FillCard(OtherHeaderText, OtherList, InvoiceCategories.Other, files, counts);
        }

        private static void FillCard(
            TextBlock? header,
            ListView? list,
            string name,
            IReadOnlyList<InvoiceFileEntry> files,
            IDictionary<string, int>? counts)
        {
            var matched = files.Where(f => string.Equals(f.Category, name, StringComparison.Ordinal)).ToList();
            if (header != null)
            {
                header.Text = $"{name}（{CountOf(name, files, counts)}，其中多页 {matched.Count(f => f.IsMultiPage)}）";
            }

            if (list != null)
            {
                list.ItemsSource = matched;
            }
        }

        private static int CountOf(string name, IReadOnlyList<InvoiceFileEntry> files, IDictionary<string, int>? counts)
        {
            if (counts != null && counts.TryGetValue(name, out var reported))
            {
                return reported;
            }

            return files.Count(f => string.Equals(f.Category, name, StringComparison.Ordinal));
        }

        /// <summary>汇总行与 classify_invoices.py 的 _summarise 对齐，好比对。</summary>
        private string SummaryOf(ClassifyReport report)
        {
            var files = report.Files ?? new List<InvoiceFileEntry>();
            var total = InvoiceCategories.All.Sum(name => CountOf(name, files, report.Counts));
            var lines = new List<string>
            {
                $"已归档 {total} 个发票（其中多页 {report.MultiPage} 个）到 {report.OutDir ?? ArchiveDir()}"
            };
            lines.AddRange(InvoiceCategories.All.Select(name => $"  {name}: {CountOf(name, files, report.Counts)}"));
            lines.Add($"  非发票（未复制）: {report.NonInvoices}");
            if (report.AlreadyArchived > 0)
            {
                // 拖进来的东西里本来就有归档目录里的副本：这不是「没认出是发票」，说清楚免得以为拖失败了
                lines.Add($"  归档目录里的副本（不再重复归档）: {report.AlreadyArchived}");
            }

            lines.Add($"  无法解析（未复制）: {report.Unreadable}");
            return string.Join(Environment.NewLine, lines);
        }

        /// <summary>票面字段只有对应票种才有值，缺就跳过，不猜。</summary>
        private static string Describe(InvoiceFields fields)
        {
            var parts = new List<string>();
            if (fields.IsTrain == true)
            {
                parts.Add("火车票");
            }
            else if (fields.IsVat == true)
            {
                parts.Add("增值税发票");
            }
            else if (fields.IsRegular == true)
            {
                parts.Add("普通发票");
            }

            if (!string.IsNullOrWhiteSpace(fields.InvoiceDate))
            {
                parts.Add(fields.InvoiceDate!);
            }

            if (!string.IsNullOrWhiteSpace(fields.InvoiceTime))
            {
                parts.Add(fields.InvoiceTime!);
            }

            if (!string.IsNullOrWhiteSpace(fields.TotalAmount))
            {
                parts.Add("¥" + fields.TotalAmount!);
            }

            if (!string.IsNullOrWhiteSpace(fields.Person))
            {
                parts.Add(fields.Person!);
            }

            if (fields.Site is { Count: >= 2 })
            {
                parts.Add($"{fields.Site[0]}→{fields.Site[1]}");
            }

            return string.Join("  ", parts);
        }

        private void AppendLog(string line)
        {
            LiveLog.Add(line);
            if (LiveLog.Count > LiveLogCap)
            {
                LiveLog.RemoveRange(0, LiveLog.Count - LiveLogCap);
            }

            if (LogBox == null)
            {
                return;
            }

            // 满批 2000 个文件时一行行 Text += 是每行整串重拷，加上每行一次强制布局，
            // 全在 UI 线程上能把窗口拖到未响应。这里攒成一批，等这一轮事件跑完再写一次。
            _displayLines.Enqueue(line);
            while (_displayLines.Count > DisplayLineCap)
            {
                _displayLines.Dequeue();
            }

            if (_flushScheduled)
            {
                return;
            }

            _flushScheduled = true;
            if (LogBox.DispatcherQueue.TryEnqueue(FlushLogBox))
            {
                return;
            }

            _flushScheduled = false;
            FlushLogBox();
        }

        /// <summary>把攒下的日志一次性写进控件；一帧里来多少行都只刷新一次。</summary>
        private void FlushLogBox()
        {
            _flushScheduled = false;
            if (LogBox == null)
            {
                return;
            }

            // 用户把视图往上拖走后不再强行拉回底部，否则每来一行就把拖动结果顶掉
            var following = LogScroll == null
                || LogScroll.ScrollableHeight <= 0.0
                || LogScroll.VerticalOffset >= LogScroll.ScrollableHeight - 1.0;

            LogBox.Text = string.Join(Environment.NewLine, _displayLines) + Environment.NewLine;

            if (following && LogScroll != null)
            {
                LogScroll.UpdateLayout();
                LogScroll.ChangeView(null, LogScroll.ScrollableHeight, null);
            }
        }

        private readonly Queue<string> _displayLines = new Queue<string>();
        private bool _flushScheduled;
        private const int DisplayLineCap = 2000;

        /// <summary>把进程级日志倒回本页控件，切页回来的那个实例才不会一片空白。</summary>
        private void ReplayLog()
        {
            if (LogBox == null || LiveLog.Count == 0)
            {
                return;
            }

            LogBox.Text = string.Join(Environment.NewLine, LiveLog) + Environment.NewLine;
            foreach (var line in LiveLog)
            {
                _displayLines.Enqueue(line);
            }
        }

        private string ArchiveDir() => LocalStore.Read(LocalStore.ArchiveDirKey, DefaultArchiveDir());

        /// <summary>是不是以管理员权限跑起来的；这会让系统拒绝接收普通权限程序发来的拖放。</summary>
        private static bool IsElevated()
        {
            try
            {
                using var identity = WindowsIdentity.GetCurrent();
                return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch (Exception)
            {
                return false;
            }
        }

        // ---------- 拖放接收：微信聊天窗口、资源管理器里的文件都能直接拽进来 ----------

        // 放置框和里面的输入框都挂了这四个处理器：用户最自然的落点是那个看得见的框，
        // 万一输入框自己把 drop 吃掉，这条路也走得通；反过来事件冒泡上来时 Handled 挡住第二遍
        private void DropZone_DragEnter(object sender, DragEventArgs e)
        {
            // Contains() 和 AvailableFormats 都是跨进程问来源，DragOver 每帧都要答案：这里问一次记下来
            var (acceptable, formats) = ProbeDraggable(e.DataView);
            _dragFormats = formats;
            _dragAcceptable = acceptable;
            e.AcceptedOperation = acceptable == true ? DataPackageOperation.Copy : DataPackageOperation.None;
            Highlight(true);
            e.Handled = true;
        }

        // AvailableFormats/Contains 是同步调用，可来源中途释放数据对象（RO_E_CLOSED）时会抛。
        // 这两个调用都在 DragEnter/DragOver 里——异常冒出同步处理器就是「拖到框上完全没反应」，
        // 所以在这里降级。日志按一次拖拽只报一遍：DragOver 每秒十几次，每次都写就把日志刷满了。
        //
        // 回的是三态：true 能放、false 确实没带文件、null 是**问不到**。问不到不能锁成 false——
        // 一次瞬时失败会把「不接受」挂在那条拖放的整个过程中（_dragAcceptable 只在 DragLeave/Drop 复位），
        // 而 AcceptedOperation=None 时 Drop 根本不会跑，连那句本该说清的降级日志都没机会落地（评审 P2）。
        // 留 null 让 DragOver 下一帧再问一次，代价只在「来源真的一直问不出」那种异常路径上重复探测。
        private (bool? Acceptable, IReadOnlyList<string> Formats) ProbeDraggable(DataPackageView view)
        {
            try
            {
                var formats = view.AvailableFormats.ToList();
                var acceptable = view.Contains(StandardDataFormats.StorageItems)
                                 || view.Contains(StandardDataFormats.Text);
                _dragProbeWarned = false;
                return (acceptable, formats);
            }
            catch (Exception ex)
            {
                if (!_dragProbeWarned)
                {
                    _dragProbeWarned = true;
                    AppendLog($"问这个来源能给什么数据时出错，先按「不能放」处理（下一帧会再问一次）：{ex.GetType().Name} {ex.Message}");
                }

                return (null, _dragFormats);
            }
        }

        private void DropZone_DragLeave(object sender, DragEventArgs e)
        {
            _dragAcceptable = null;
            Highlight(false);
            e.Handled = true;
        }

        private void DropZone_DragOver(object sender, DragEventArgs e)
        {
            // 没有先收到 Enter 时（在两个挂处理器之间挪动）退回现场问一次，并把答案记下来，
            // 免得每帧都跨进程问一遍
            var acceptable = _dragAcceptable ?? ProbeDraggable(e.DataView).Acceptable;
            // 只有问出个准数（true/false）才锁住；null 是「这一帧没问到」，下一帧再问
            if (acceptable.HasValue)
            {
                _dragAcceptable = acceptable;
            }

            e.AcceptedOperation = acceptable == true ? DataPackageOperation.Copy : DataPackageOperation.None;

            if (e.DragUIOverride != null)
            {
                e.DragUIOverride.IsCaptionVisible = true;
                // 拒绝时把原因写在光标旁边：只给一个禁止符号，使用者看不出差在哪。
                // 「问不到」和「确实没带文件」是两件事，别拿后者的话去搪塞前者
                e.DragUIOverride.Caption = acceptable == true ? "添加发票"
                    : acceptable == false ? "这个来源没给出文件，只能收 PDF"
                    : "没问到这个来源能给什么，再拖一次试试";
                e.DragUIOverride.IsContentVisible = true;
            }

            e.Handled = true;
        }

        private async void DropZone_Drop(object sender, DragEventArgs e)
        {
            Highlight(false);
            _dragAcceptable = null;
            // 输入框和放置框都挂了处理器：先在最近的那个上认下这次 drop，别让冒泡再跑第二遍
            e.Handled = true;
            // 取文件清单是异步的，不挂上 deferral 的话 WinUI 可能在我们拿到路径之前就把这次投递判完成
            var deferral = e.GetDeferral();
            try
            {
                var read = ReadDroppedPathsAsync(e.DataView);
                if (!await FinishedWithinTimeoutAsync(read))
                {
                    AppendLog($"这个来源 {DropReadTimeout.TotalSeconds:0} 秒内没给出文件清单（多半是那张票还在下载、或云端占位还没落盘），这次先不收；等它好了再拖一次");
                    SetDropHint("这个来源没及时给出文件清单，这次先没收");
                    return;
                }

                var (paths, storageError, lostItems, storageCount, sawStorageItems) = await read;
                if (paths.Count == 0)
                {
                    // 三种「一个都没收到」的原因不一样，混成一句会把人往错的方向引：
                    // 清单里每一项都没给路径 / 整份清单读不出来 / 来源压根没给文件
                    AppendLog(lostItems > 0
                        ? $"这次拖进来的 {lostItems} 项都没给出可用的路径：{storageError}"
                          + $"（来源提供的数据格式：{(_dragFormats.Count == 0 ? "无" : string.Join("、", _dragFormats))}）"
                        : storageError != null
                            ? $"这个来源的文件清单读不出来：{storageError}"
                            // 来源**声称**给了文件清单却一项都没渲染出来（数据对象在它释放前就没了）：
                            // 这时候说「这个来源只给了链接或缩略图」是把人往错的方向引（实测撞到过一次）
                            : sawStorageItems
                                ? "来源说有文件清单，却一项都没给出来（多半是它在数据对象释放前没渲染完）：重新拖一次，或先在资源管理器里打开那个文件夹再拖其中的 PDF"
                                  + $"（来源提供的数据格式：{(_dragFormats.Count == 0 ? "无" : string.Join("、", _dragFormats))}）"
                                : "拖放没带回任何路径：这个来源只给了链接或缩略图，请在聊天窗口先「打开文件夹」再拖其中的 PDF"
                                  + $"（来源提供的数据格式：{(_dragFormats.Count == 0 ? "无" : string.Join("、", _dragFormats))}）");
                    return;
                }

                // 半截清单冒充齐全是最坑的一种静默失败：拖了 5 张只进 3 张，界面还显示「收到 3 个 PDF」，
                // 使用者会以为那 2 张在服务那边被过滤掉了。少收了就必须单独说一句。
                // 数字取「文件清单那一步」的收/丢，文本兜底补回来的另算，不然 N+M 和实际拖的张数对不上
                if (lostItems > 0)
                {
                    AppendLog($"注意：这次拖进来的文件清单里有 {lostItems} 项取不到路径"
                              + $"（清单里认出 {storageCount} 项，加上文本兜底一共 {paths.Count} 项）"
                              + (storageError == null ? "" : $"；首个原因：{storageError}"));
                }

                await ApplyDropAsync(paths);
            }
            catch (Exception ex)
            {
                // async void 里漏出去的异常是直接崩掉应用，这里宁可只留一行日志
                AppendLog($"处理拖放时出错：{ex.GetType().Name} {ex.Message}");
            }
            finally
            {
                deferral.Complete();
            }
        }

        // 拖不动时的退路：微信有些版本把聊天气泡拖出来只给一段链接文本，落到框里什么也没有；
        // 那里 Ctrl+C 再点「粘贴文件」，读到的是同一个 DataPackageView，所以取路径的逻辑一行都没多写。
        // 这条也是接收端唯一能在本机自动验证的路——OLE「松手落下」那一下模拟不了（见 README）。
        private async void PasteButton_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                var read = ReadDroppedPathsAsync(Clipboard.GetContent());
                if (!await FinishedWithinTimeoutAsync(read))
                {
                    AppendLog($"剪贴板里的清单 {DropReadTimeout.TotalSeconds:0} 秒内没读出来（来源可能还占着剪贴板），这次先不粘");
                    return;
                }

                var (paths, storageError, lostItems, storageCount, sawStorageItems) = await read;
                if (paths.Count == 0)
                {
                    AppendLog(storageError != null
                        ? $"剪贴板的内容读不出来：{storageError}"
                        // 和拖放那侧同一个口径：来源说它给了文件清单却一项都没渲染出来，
                        // 这时候让用户「先去 Ctrl+C」是指错了方向
                        : sawStorageItems
                            ? "剪贴板说有文件清单，却一项都没给出来（来源可能在释放数据对象前没渲染完）：再 Ctrl+C 一次然后点这里"
                            : "剪贴板里没有可认出的路径：先在资源管理器或微信里 Ctrl+C 一个 PDF 或文件夹，再点这里");
                    return;
                }

                // 和拖放一样：少收了要单独说，不能把残缺清单当齐全
                AppendLog($"从剪贴板读到 {paths.Count} 项（清单里认出 {storageCount} 项，文本兜底补到 {paths.Count} 项）"
                          + (lostItems > 0
                              ? $"，另有 {lostItems} 项取不到路径"
                                + (storageError == null ? string.Empty : $"；首个原因：{storageError}")
                              : string.Empty));
                await ApplyDropAsync(paths);
            }
            catch (Exception ex)
            {
                // 别的进程正锁着剪贴板时读会抛；async void 里漏出去就是直接崩掉应用
                AppendLog($"读剪贴板时出错：{ex.GetType().Name} {ex.Message}");
            }
        }

        // 问来源要清单的时限。GetStorageItemsAsync 是跨进程回头问来源，微信那张还在下载的票、
        // 网盘还没落盘的占位，都能让它一直挂着；不封顶这次投递就被无限吊着——拖影不消失、来源那边
        // 等不到 IDropSource 回调，用户看到的是「界面卡住了」，而且完全不知道卡在哪一步（评审 P3）
        private static readonly TimeSpan DropReadTimeout = TimeSpan.FromSeconds(20);

        /// <summary>在时限内等到就返回 true；超时返回 false，调用方自己把话说清楚。</summary>
        private static async Task<bool> FinishedWithinTimeoutAsync(Task task)
        {
            if (await Task.WhenAny(task, Task.Delay(DropReadTimeout)) == task)
            {
                return true;
            }

            // 放弃之后这一条还是可能晚点失败；把异常看一眼，别留成没人知道的未观察异常
            _ = task.ContinueWith(static t => _ = t.Exception, TaskContinuationOptions.OnlyOnFaulted);
            return false;
        }

        /// <summary>
        /// 从拖放数据里取路径。资源管理器和微信都给 StorageItems；有的程序只肯给一段文本
        /// （路径或链接），那条路也试一下，两条都取不到就算了。
        /// StorageItems 读失败时把原因带回去，好和「来源压根没给文件」区分开；
        /// 取不到路径的项数单独用 LostItems 报出来——只收到一部分不等于全收到；
        /// StorageCount 是「文件清单那一步」收到的项数，文本兜底补进来的另算，界面上两个数字分开写。
        /// </summary>
        private static async Task<(List<string> Paths, string? StorageError, int LostItems, int StorageCount, bool SawStorageItems)> ReadDroppedPathsAsync(DataPackageView view)
        {
            var paths = new List<string>();
            string? storageError = null;
            var lostItems = 0;
            var storageCount = 0;
            var sawStorageItems = false;

            if (ContainsSafe(view, StandardDataFormats.StorageItems, ref storageError))
            {
                sawStorageItems = true;
                try
                {
                    // 每项怎么取路径交回 DropInbox 统一处理：坏一项只丢那一项，其余照常收，
                    // 少收了多少由 LostItems 带出去，不在这条循环里悄悄吞掉
                    var items = await view.GetStorageItemsAsync();
                    var (collected, lost, error) = DropInbox.CollectPaths(
                        items.Select(item => new Func<string?>(() => item.Path)));
                    paths.AddRange(collected);
                    storageCount = collected.Count;
                    lostItems = lost;
                    storageError = error;
                }
                catch (Exception ex)
                {
                    // 整份清单都拿不到（受保护目录、数据对象已释放）：一项都没有，交给下面的文本兜底
                    storageError = $"{ex.GetType().Name} {ex.Message}";
                }
            }

            // 少收了也要试文本兜底：只按 paths.Count==0 判断的话，
            // 一个来源同时给了 StorageItems（坏了几项）和 Text 时，能补的项就白白丢了
            if ((paths.Count == 0 || lostItems > 0) && ContainsSafe(view, StandardDataFormats.Text, ref storageError))
            {
                try
                {
                    // 认路径 + 去重都在 DropInbox.MergeTextFallback（纯函数，harness 里单独测）：
                    // 文本里常常又有清单已给过的那几项，直接并进来会让「只拖一个文件夹」的早退失效
                    paths = DropInbox.MergeTextFallback(paths, await view.GetTextAsync());
                }
                catch (Exception ex)
                {
                    // 数据对象已释放时 GetTextAsync 自己会抛。不接住的话异常冒出这个方法，
                    // 上层只能整批丢掉——连已经拿到的那几项也没了，表现成「拖了没反应」
                    storageError ??= $"{ex.GetType().Name} {ex.Message}";
                }
            }

            return (paths, storageError, lostItems, storageCount, sawStorageItems);
        }

        /// <summary>
        /// 问「这份数据里有没有某个格式」。DataPackageView.Contains 是跨进程去问来源，
        /// 来源中途把数据对象释放掉（RO_E_CLOSED）就抛——它必须在这儿接住：异常要是冒出去，
        /// 整批拖连已经拿到的一项都不剩，后面的文本兜底也走不到，表现成「拖了没反应」。
        /// 拖拽悬停那侧同样的调用由 ProbeDraggable 兜着，读取这侧之前是裸的。
        /// </summary>
        private static bool ContainsSafe(DataPackageView view, string format, ref string? error)
        {
            try
            {
                return view.Contains(format);
            }
            catch (Exception ex)
            {
                error ??= $"{ex.GetType().Name} {ex.Message}";
                return false;
            }
        }

        /// <summary>怎么解释这批路径由 DropInbox 算（纯逻辑，可单独验证），这里只把结果写进控件。</summary>
        private async Task ApplyDropAsync(List<string> paths)
        {
            // 展开文件夹要读磁盘：一个断掉的网上邻居或一棵超大的树能在 UI 线程上冻几十秒，
            // 所以算的过程放后台，回到这里时只剩写控件
            var exclude = new List<string> { ArchiveDir() };
            // 这里只「记」不「增」。计数得能拦住「用户点了清除／改了源目录」，但那两件事自有地方去顶它；
            // 自己在入口顶一下，就会把**另一条还在等磁盘的拖放**判成作废——微信一次只给一个文件，
            // 连着拖两张是常态，谁先落地谁后落地不该互相吃掉（评审 P1）
            var seq = _dropSeq;
            var plan = await Task.Run(() => DropInbox.Plan(paths, exclude));

            // 等磁盘枚举这段时间里用户可能已经切了页：控件挂在被卸掉的那一页上，写进去等于没写，
            // 而看得见的那一页还停在旧数字上（评审 P2）。所以结果交给「当前活着的那一页」去落地
            (_active ?? this).ApplyDropResult(plan, paths, seq);
        }

        /// <summary>把 Plan 的结果落到这一页的控件上；清单与日志是进程级的，跨页也认。</summary>
        private void ApplyDropResult(DropPlan plan, List<string> paths, int seq)
        {
            if (seq != _dropSeq)
            {
                // 等磁盘枚举这段时间里用户点了「清除」或改了目录，这次拖放作废——
                // 作废必须说出来，不然用户看到的是「拖了没反应」，还会再拖一次
                SetDropHint("这次拖放作废（期间改了源目录或点了「清除」），清单没变");
                AppendLog($"  拖入的 {paths.Count} 项被作废：等待期间源目录改了或点了「清除」");
                return;
            }

            // 逐张拖是微信的唯一用法（它一次只给一个文件），整批替换会静默丢掉前面拖进来的那些。
            // 清单是进程级的，切页重建的这一页在构造时就把「累计 N 个」重新显示出来（见构造函数）
            var added = 0;
            // 键集合先建好，累加时 O(1) 查：清单里挂着一两千个、这一轮又拖进来一两千个时，
            // 逐项线性比（每比一次两回 GetFullPath）会把 UI 线程钉住好几秒（评审 P2）
            var keys = new HashSet<string>(_droppedFiles.Select(f => DropInbox.Key(f)), StringComparer.OrdinalIgnoreCase);
            foreach (var file in plan.Files)
            {
                if (keys.Add(DropInbox.Key(file)))
                {
                    _droppedFiles.Add(file);
                    added++;
                }
            }

            // 同一个文件重复拖（比如连着拖了两次同一张）只算一次，差值就是被去重掉的那几个
            var skipped = plan.Files.Count - added;

            if (plan.Folder != null && FolderPathBox != null)
            {
                // 只拖一个文件夹 = 选目录。清单里原本挂着散文件的话，这里要**明说**是改成整目录扫描、
                // 把它们去掉，而不是靠 TextChanged 晚一拍去「顺手」抹掉（那还会把原因错记成用户改了目录）
                if (_droppedFiles.Count > 0)
                {
                    AppendLog($"改成整目录扫描：之前清单里的 {_droppedFiles.Count} 个 PDF 已去掉（只拖一个文件夹等同于选目录）");
                    _droppedFiles.Clear();
                    if (ClearDropButton != null)
                    {
                        ClearDropButton.Visibility = Visibility.Collapsed;
                    }
                }

                _programmaticFolder = plan.Folder;
                FolderPathBox.Text = plan.Folder;
                // 顺手把这个目录记住：不写的话重启回到这一页时源目录是空的，
                // 而用户明明刚刚把那个文件夹拖进过框里（评审 P3）
                LocalStore.Write(LocalStore.SourceFolderKey, plan.Folder);
            }

            // 提示说累计待处理的数字，而不是本轮那几个：连拖多张时只报本轮会让人以为只收到最后一张
            if (_droppedFiles.Count > 0 && added != _droppedFiles.Count)
            {
                SetDropHint($"累计 {_droppedFiles.Count} 个 PDF 待处理（本轮新增 {added} 个"
                            + (skipped > 0 ? $"，{skipped} 个之前已经在清单里" : string.Empty)
                            + "），点「开始分类」处理");
            }
            else
            {
                SetDropHint(plan.Hint);
            }

            AppendLog(plan.Log);
            // 只有真出现去重/累加时才补这一行，第一次单独拖不需要看两句重复数字
            if (added != plan.Files.Count)
            {
                AppendLog($"  本轮新增 {added} 个，累计 {_droppedFiles.Count} 个待处理");
            }

            if (plan.EnumerateError != null)
            {
                AppendLog($"  文件夹没读完整：{plan.EnumerateError}");
            }

            if (ClearDropButton != null)
            {
                ClearDropButton.Visibility = _droppedFiles.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            }
        }

        // 拖入清单与用户操作之间的「谁最后说话」计数：展开文件夹是后台跑的，回来时若用户已经点了清除或改了目录，
        // 这一次拖放的结果就该作废，不能把用户刚清掉的清单又塞回去。清单既然进程级共享，计数也得进程级共享，
        // 否则另一个页面上发生的「清除」拦不住这一页在途的结果
        private static int _dropSeq;

        // 界面自己往源目录框里写值时（拖进一个文件夹 = 选目录）要挡住这次的 TextChanged：
        // WinUI 的 TextChanged 是晚一拍到达的，同步布尔挡不住——等它到的时候标志已经复位成 false，
        // 于是「界面自己的写入」被当成「用户改了源目录」：白 ++ 一次代号会把并发的另一次拖放判成作废，
        // 还可能在日志里把原因写成用户改了目录。改成**记值不记时刻**：
        // 框里的文本等于界面刚写进去的那个值，就把这一次消化掉，不依赖事件什么时候到。
        private static string? _programmaticFolder;

        private void FolderPathBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            // 界面自己写进去的那个值：匹配上就是这次写入带来的事件，消化掉，不当成用户改目录
            var now = FolderPathBox?.Text ?? string.Empty;
            if (_programmaticFolder != null && now == _programmaticFolder)
            {
                _programmaticFolder = null;
                return;
            }

            _programmaticFolder = null;

            _dropSeq++;
            if (_droppedFiles.Count == 0)
            {
                return;
            }

            _droppedFiles.Clear();
            if (ClearDropButton != null)
            {
                ClearDropButton.Visibility = Visibility.Collapsed;
            }

            AppendLog("改了源目录，之前拖入的文件已清掉（需要的话再拖一次）");
        }

        private void ClearDropButton_Click(object sender, RoutedEventArgs e)
        {
            _dropSeq++;
            _droppedFiles.Clear();
            if (ClearDropButton != null)
            {
                ClearDropButton.Visibility = Visibility.Collapsed;
            }

            SetDropHint("已清除拖入的文件");
        }

        private void SetDropHint(string text)
        {
            if (DropHintText != null)
            {
                DropHintText.Text = text;
            }
        }

        private void Highlight(bool on)
        {
            if (DropZone != null)
            {
                DropZone.BorderThickness = new Thickness(on ? 2 : 1);
            }
        }

        // 归档位置由 Python 侧决定，服务没回传时按 README 的约定从仓库根往下找一次
        private static string DefaultArchiveDir()
        {
            // 判据是「哪个祖先目录里放着 invoice_recognition」，而不是「归档目录已经建出来了没有」：
            // 第一次跑的时候服务还没把 发票分类 建出来，这时也必须回**绝对路径**——回相对路径的话
            // UnderAny 会按进程当前目录去解释它，「归档里的副本不再归档」这条排除就静默匹配不到，
            // 归档目录里的文件会吃掉 2000 的额度，那句「N 个是归档目录里的副本」也永远不出现（评审 P3）
            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
            {
                if (Directory.Exists(Path.Combine(dir.FullName, "invoice_recognition")))
                {
                    return Path.Combine(dir.FullName, "invoice_recognition", "temp", "发票分类");
                }
            }

            return Path.Combine(AppContext.BaseDirectory, "invoice_recognition", "temp", "发票分类");
        }
    }
}

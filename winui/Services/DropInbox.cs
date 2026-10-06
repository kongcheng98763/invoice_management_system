using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace winui.Services
{
    /// <summary>一次拖放的解释结果：要么等同于选了个目录，要么是要发给服务的一批 PDF。</summary>
    public sealed class DropPlan
    {
        /// <summary>只拖进一个文件夹时给出它的路径（走整目录扫描），其余情况为 null</summary>
        public string? Folder { get; set; }

        /// <summary>要作为 files 发给服务的 PDF 绝对路径</summary>
        public List<string> Files { get; } = new List<string>();

        public int Folders { get; set; }

        public int Pdfs { get; set; }

        public int NonPdf { get; set; }

        /// <summary>拖进来但既不是已存在的文件也不是已存在的目录的项（比如只给了链接）</summary>
        public int NoPath { get; set; }

        /// <summary>
        /// 没取到路径的项里有多少个「看着就是 .pdf」：这类和「拖进来的本来不是 PDF」是两回事，
        /// 混成一句会把人说误导去翻文件名。实际原因通常是来源只给了链接、缩略图或还没落盘的云端占位。
        /// </summary>
        public int PdfLikeMissing { get; set; }

        /// <summary>本来就躺在归档目录里的副本，跳过不再归档</summary>
        public int AlreadyArchived { get; set; }

        /// <summary>超出单次上限、这一轮没发给服务的文件数（按枚举顺序砍掉尾部）</summary>
        public int Truncated { get; set; }

        /// <summary>展开文件夹时中途读不下去（没权限、路径过长、网络盘断了）的原因；null 表示一路读到底</summary>
        public string? EnumerateError { get; set; }

        public string Hint { get; set; } = string.Empty;

        public string Log { get; set; } = string.Empty;
    }

    /// <summary>
    /// 把「拖进来的一堆路径」翻译成 DropPlan。纯计算 + 只读文件系统，不碰任何控件，
    /// 所以能脱离界面单独验证；界面侧只负责把结果写进控件。
    /// 要读目录，调用方请放到后台线程上（网络盘或大目录可能几秒到几分钟）。
    /// </summary>
    public static class DropInbox
    {
        /// <summary>
        /// 一次拖放最多发给服务多少个文件，要和 invoice_service.py 里的 MAX_FILES 对上：
        /// 超了服务会整批拒绝（HTTP 400），一个都归档不了，所以这里先砍到上限并把砍掉的数量说出来。
        /// </summary>
        public const int MaxFiles = 2000;

        /// <param name="paths">拖放拿到的路径清单</param>
        /// <param name="excludeRoots">归档目录之类：里面的副本不该再被归档一次</param>
        public static DropPlan Plan(IReadOnlyList<string> paths, IReadOnlyList<string>? excludeRoots = null)
        {
            // 先按「同一个文件就同一个键」合一遍：来源给的写法不统一（微信、资源管理器、文本兜底
            // 各写各的），同一个文件换种写法就会被算成两项——发给服务是同一张票归档两份，
            // 而「C:\inv」和「C:\inv\」被数成两个目录时，「只拖一个文件夹」的早退也一并失效（评审 P1）
            paths = Distinct(paths);
            var plan = new DropPlan();
            // 只认「已经在磁盘上存在的路径」：来源（微信、资源管理器）有时只给个链接或还没落盘的占位。
            // File.Exists 对「存在但 ACL 拒读」答 false（那种情况有 ProbeReadable 和 NoPath 计数报出来）；
            // 「超过 260 字符就答 false」这条评审提过的担心在 .NET 这侧不成立（temp\longpath_manifest_probe.py），
            // 但 Python 服务那侧真的读不动，由 classify_invoices.fs_path() 换成原样通道写法
            var folders = paths.Where(p => Directory.Exists(p)).ToList();
            var loose = paths.Where(p => File.Exists(p)).ToList();
            var archived = loose.Where(p => UnderAny(p, excludeRoots)).ToList();
            var pdfs = loose
                .Where(p => IsPdf(p) && !archived.Contains(p))
                .ToList();

            // 下面这几个数字描述的是「拖进来的那堆东西」，不是最终发给服务的清单：
            // 截断只改 plan.Files，这些计数故意不重算，所以超上限时日志里两个数字会并排出现
            plan.Folders = folders.Count;
            plan.Pdfs = pdfs.Count;
            plan.NonPdf = loose.Count - pdfs.Count - archived.Count;
            plan.AlreadyArchived = archived.Count;
            plan.NoPath = paths.Count - folders.Count - loose.Count;
            plan.PdfLikeMissing = paths.Count(p => !File.Exists(p) && !Directory.Exists(p)
                                                   && IsPdf(p));

            // 拖放结果一律留一行日志：只写提示文字的话，用户分不清「没生效」和「生效但来源没给路径」
            plan.Log = $"拖入 {paths.Count} 项：文件夹 {folders.Count} 个、PDF {pdfs.Count} 个"
                       + (plan.NonPdf > 0 ? $"，非 PDF 文件 {plan.NonPdf} 个不收" : string.Empty)
                       + (plan.AlreadyArchived > 0 ? $"，{plan.AlreadyArchived} 个是归档目录里的副本，跳过" : string.Empty)
                       + (plan.NoPath > 0
                          ? $"，另有 {plan.NoPath} 项没取到路径"
                            + (plan.PdfLikeMissing > 0
                                ? $"（其中 {plan.PdfLikeMissing} 个后缀是 .pdf：多半是来源只给了还没落盘的占位或云端文件，不是文件名写错）"
                                : string.Empty)
                          : string.Empty);

            if (folders.Count == 1 && loose.Count == 0)
            {
                // 只拖一个文件夹就等同于「选目录」：交给服务自己递归，既不受 MaxFiles 管，也不在这里展开，
                // 所以 AlreadyArchived/截断 这些数字都还是 0。服务扫不动时现在会报「目录读不动」，
                // 但那是几十秒后的事，这里先探一下根目录能不能列，拖完立刻说清楚
                plan.Folder = folders[0];
                var blocked = ProbeReadable(folders[0]);
                plan.Hint = blocked == null
                    ? $"已选择拖入的文件夹：{folders[0]}"
                    : $"拖进来的文件夹读不动：{blocked}";
                if (blocked != null)
                {
                    plan.EnumerateError = $"{folders[0]}：{blocked}";
                    plan.Log += "，但这个文件夹读不动，扫起来也不会有结果";
                }
                return plan;
            }

            var errors = new List<string>();
            // 先放用户亲手挑出来的散文件，再放文件夹里展开的：一批塞不下时砍的是尾部，
            // 顺序反了就会把「专门拖进来的那几张」丢掉、留下文件夹里按名字序的前一堆
            plan.Files.AddRange(pdfs);
            // 已经收下的先连键一起收进集合，展开出来的每一项 O(1) 查，不再两两 SamePath（评审 P2）
            var seen = new HashSet<string>(plan.Files.Select(Key), StringComparer.OrdinalIgnoreCase);
            foreach (var folder in folders)
            {
                var (found, error) = EnumeratePdfs(folder);
                foreach (var one in found.Where(p => !UnderAny(p, excludeRoots)))
                {
                    // 展开出来的要和用户亲手拖进来的散文件按同一个键比：
                    // `C:/inv/a.pdf`（文本兜底那种写法）和 `C:\inv\a.pdf`（枚举出来的）是同一张票，
                    // 只按原串比就会两张都留下、归档成 a.pdf 和 a(1).pdf（评审 P2）
                    if (seen.Add(Key(one)))
                    {
                        plan.Files.Add(one);
                    }
                }
                if (error != null)
                {
                    errors.Add($"{folder}：{error}");
                }
            }

            // 已经枚举到一半才出错：留着已拿到的部分，同时把原因说出来，不能假装这个文件夹是空的
            if (errors.Count > 0)
            {
                plan.EnumerateError = string.Join("; ", errors);
                plan.Log += $"，有 {errors.Count} 个文件夹没读完整";
            }

            // 这里不再补一遍去重：散文件那批进来前先过 Distinct（按 Key 收），文件夹展开出来的那批
            // 逐项查过同一个键集合，合起来按键必然唯一。原先这次线性 RemoveAll 既按原串比
            // （`C:/a.pdf` 和 `C:\a.pdf` 这种同一张票会双双留下），又是一遍 O(n·m) 的白跑（评审 P2）

            if (plan.Files.Count > MaxFiles)
            {
                // 服务对超上限的报文是**整批 400**，一个都不归档，所以宁可砍尾也要把数量说出来；
                // 砍尾留下的是前面的散文件（见上面的 AddRange 顺序），被丢的那批没有单独清单
                var total = plan.Files.Count;
                plan.Truncated = total - MaxFiles;
                plan.Files.RemoveRange(MaxFiles, plan.Truncated);
                plan.Log += $"，一共认出 {total} 个 PDF，只发前 {MaxFiles} 个，其余 {plan.Truncated} 个这次不发";
            }

            plan.Hint = plan.Files.Count == 0
                ? plan.EnumerateError != null
                    ? "拖进来的文件夹没读出来：详见日志里的原因"
                    : plan.PdfLikeMissing > 0
                        ? $"拖进来的 {plan.PdfLikeMissing} 个 .pdf 取不到路径（多半是还没落盘的占位或云端文件），没收到可处理的文件"
                        // 全是归档副本时不能说「没有 PDF」：日志已经写了「N 个是归档目录里的副本，跳过」，
                        // 两句对着吵，用户不知道该信哪句
                        : plan.AlreadyArchived > 0
                            ? $"拖进来的 {plan.AlreadyArchived} 份都是归档目录里已有的副本，按约定不再归档第二次"
                            : "拖进来的没有 PDF：只处理 .pdf 文件，拖文件夹会连它里面的 PDF 一起收"
                : $"已拖入 {plan.Files.Count} 个 PDF，点「开始分类」处理"
                  + (plan.Truncated > 0
                      ? $"（一次最多 {MaxFiles} 个，其余 {plan.Truncated} 个没进来，请分开拖）"
                      : string.Empty)
                  + (plan.EnumerateError != null ? "（有文件夹没读完整，见日志）" : string.Empty);
            return plan;
        }

        /// <summary>
        /// 逐项取路径：坏的那一项单独计数、不连累其余，最后把「收到几项」和「坏了几项」都带回去。
        /// 拖放来源是跨进程的（微信、资源管理器）：某一项可能中途释放数据对象（RO_E_CLOSED）、
        /// 被系统拒绝、或压根没有文件系统路径（回收站、没落盘的云端占位）。
        /// 那种情况下把半截清单当齐全最坑——拖了 5 张只进 3 张，界面还显示「收到 3 个」，
        /// 使用者会以为另外 2 张是服务那边过滤掉的。
        /// 取路径的动作之所以传成委托：真正的跨进程调用发生在属性访问那一刻，界面无须知道细节。
        /// </summary>
        /// <param name="pathGetters">每项一个「去问来源要路径」的动作</param>
        public static (List<string> Paths, int Lost, string? Error) CollectPaths(IEnumerable<Func<string?>> pathGetters)
        {
            var paths = new List<string>();
            var lost = 0;
            string? error = null;

            foreach (var getPath in pathGetters)
            {
                try
                {
                    var path = getPath();
                    if (string.IsNullOrEmpty(path))
                    {
                        // 不抛但给空串：按 API 是成功，按用户却是「这个文件没进清单」，得算坏项
                        lost++;
                        error ??= "来源没给出这一项的路径";
                    }
                    else
                    {
                        paths.Add(path);
                    }
                }
                catch (Exception ex)
                {
                    lost++;
                    // 留「第一个」原因而不是最后一个：界面上那句后面跟的是首个原因，
                    // 写成最后一个会让用户以为整批都是同一种错，实际第一项可能是别的原因
                    error ??= $"{ex.GetType().Name} {ex.Message}";
                }
            }

            return (paths, lost, error);
        }

        /// <summary>
        /// 文本兜底：来源不给文件清单、只给一段文本时从里面认路径。
        /// 按换行和分号拆（CF_HDROP 转成文本时用分号连接，代价是路径里真带分号会被拆错，
        /// Windows 允许但票样里没有），去掉包裹的引号，file:// 还原成本地路径，
        /// http 之类和不像路径的纯文字丢掉。
        /// </summary>
        public static List<string> PathsFromText(string text)
        {
            var paths = new List<string>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var raw in text.Split(new[] { '\r', '\n', ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var one = raw.Trim().Trim('"').Trim();
                if (one.Length == 0)
                {
                    continue;
                }

                if (one.Contains("://"))
                {
                    if (!Uri.TryCreate(one, UriKind.Absolute, out var uri) || !uri.IsFile)
                    {
                        continue;           // 网页链接之类，不是本地文件
                    }

                    one = uri.LocalPath;
                }
                else if (!one.Contains('\\') && !one.Contains('/'))
                {
                    continue;               // 聊天窗口里拖出来的一句话
                }

                if (seen.Add(Key(one)))
                {
                    paths.Add(one);
                }
            }

            return paths;
        }

        /// <summary>
        /// 把文本兜底认出来的路径并回清单。清单坏了几项时来源那段文本里往往就是同一批东西：
        /// 两种格式各带一份是常态（拖一个文件夹时 CF_HDROP 和 Text 都会带上它），
        /// 直接 AddRange 会让同一个目录出现两次，DropInbox 的「只拖一个文件夹」早退就此失效——
        /// 改走 files 清单、撞上单次上限、源目录框也不会被填上。所以按忽略大小写去重再并。
        /// </summary>
        public static List<string> MergeTextFallback(IReadOnlyList<string> collected, string? text)
        {
            var merged = new List<string>(collected);
            var seen = new HashSet<string>(collected.Select(Key), StringComparer.OrdinalIgnoreCase);
            if (string.IsNullOrWhiteSpace(text))
            {
                return merged;
            }

            foreach (var one in PathsFromText(text))
            {
                // 按收过的键去重：清单给「C:\inv」、文本给「C:\inv\」是同一件事，
                // 当成两项就没法走「只拖一个文件夹」那条早退了
                if (seen.Add(Key(one)))
                {
                    merged.Add(one);
                }
            }

            return merged;
        }

        /// <summary>
        /// 把一条路径收成「同一个文件就同一个键」的写法：正斜杠、`.`／`..`、结尾多出来的一根杠、
        /// `\\?\` 原样通道前缀都在这一步抹平（比较一律再按忽略大小写做）。
        /// 覆盖不到的是 8.3 短名（`CXQ~1`）——展开它要一次 Win32 调用，而 shell 只在超过 260 的
        /// 目录上才会回吐短名，那种路径服务侧照样读得动（fs_path 换写法），所以这里不追。
        /// </summary>
        public static string Normalize(string? path)
        {
            var p = (path ?? string.Empty).Trim().Trim('"').Trim();
            if (p.Length == 0)
            {
                return string.Empty;
            }

            // 原样通道只吃绝对路径，而且 GetFullPath 会把这个前缀原样保留下来，得先摘掉
            if (p.StartsWith(@"\\?\", StringComparison.Ordinal))
            {
                p = p.StartsWith(@"\\?\UNC\", StringComparison.Ordinal)
                    ? @"\\" + p.Substring(8)
                    : p.Substring(4);
            }

            // 和 UnderAny 用同一个 Full：畸形路径（非法字符那种）按原文合，
            // 这一层宁可把一项判成两项，也不能抛出去把整批拖放带走
            p = Full(p) ?? p;

            p = p.Replace('/', '\\');
            // Win32 会把名字结尾的点和空格剥掉，所以「票.pdf」和「票.pdf.」是同一个文件，
            // 收成键时必须一起剥掉，否则它们算两项、同一张票会被归档两份（评审 P2）
            var trimmed = p.TrimEnd('.', ' ').TrimEnd('\\');
            // 根目录那根杠要留着（C:\ 和 C: 不是同一个键）
            return trimmed.Length == 2 && trimmed[1] == ':' ? trimmed + "\\" : trimmed;
        }

        /// <summary>按 Normalize 过的键去重，保留来源给的第一种写法。</summary>
        public static List<string> Distinct(IReadOnlyList<string> paths)
        {
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var kept = new List<string>(paths.Count);
            foreach (var one in paths)
            {
                if (seen.Add(Normalize(one)))
                {
                    kept.Add(one);
                }
            }

            return kept;
        }

        /// <summary>
        /// 后缀判断走这里，别直接对原串 `EndsWith(".pdf")`：Windows 认为「发票.pdf.」和「发票.pdf」
        /// 是同一个文件（结尾的点会被剥掉，`File.Exists` 答 True），照原串判就会把一张真票拒成
        /// 「非 PDF 文件不收」（评审 P3）。顺带把结尾的空格也剥掉，规则和处理短名那类保持一样保守：
        /// 只影响"怎么认后缀"，不动路径本身，去重键仍按 Normalize 说话。
        /// </summary>
        public static bool IsPdf(string? path)
            => (path ?? string.Empty).TrimEnd('.', ' ').EndsWith(".pdf", StringComparison.OrdinalIgnoreCase);

        /// <summary>两条写法是否指同一个东西（比较前按 Normalize 收成同一个键）。</summary>
        public static bool SamePath(string? a, string? b)
            => string.Equals(Normalize(a), Normalize(b), StringComparison.OrdinalIgnoreCase);

        /// <summary>
        /// 同一东西的键：成对比较走 SamePath，一堆里查重必须走这里配 HashSet。
        /// SamePath 每比一次要做两回 Normalize（内含 GetFullPath），拿它在列表里线性扫是 O(n·m)：
        /// 从文件夹一次拖两千个 PDF 时能把 UI 线程钉住好几秒，看着就是「拖进去没反应」（评审 P2）。
        /// </summary>
        public static string Key(string? path) => Normalize(path);

        /// <summary>
        /// 这个目录能不能列出来：只看第一项取得到取不到（空目录算能读）。
        /// 目的不是数文件，是拖完立刻告诉用户「这个文件夹我读不动」，而不是等扫完报 0 个。
        /// </summary>
        private static string? ProbeReadable(string folder)
        {
            try
            {
                foreach (var ignored in Directory.EnumerateFileSystemEntries(folder))
                {
                    break;
                }

                return null;
            }
            catch (Exception ex)
            {
                return $"{ex.GetType().Name} {ex.Message}";
            }
        }

        /// <summary>
        /// 递归收 PDF。不用 EnumerateFiles(AllDirectories)：它照单跟随 junction 和目录软链，
        /// 一个自指向的链接就能让枚举无限套下去（实测几秒内吐出上百万条路径），
        /// 所以这里自己走一遍并按目录的真实身份去重。出错时把已经收到的部分带回来，
        /// 别让一个读不动的子目录清空结果。
        /// </summary>
        private static (List<string> Files, string? Error) EnumeratePdfs(string folder)
        {
            const int MaxDirectories = 20000;
            const int MaxEnumeratedFiles = 20000;

            var found = new List<string>();
            var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var pending = new Queue<string>();
            pending.Enqueue(folder);

            try
            {
                while (pending.Count > 0)
                {
                    if (visited.Count >= MaxDirectories || found.Count >= MaxEnumeratedFiles)
                    {
                        return (found, $"超过 {MaxDirectories} 个子目录或 {MaxEnumeratedFiles} 个文件，剩下的没读");
                    }

                    DirectoryInfo current;
                    try
                    {
                        current = new DirectoryInfo(pending.Dequeue());
                    }
                    catch (Exception)
                    {
                        continue;           // 路径本身非法（过长、含非法字符）
                    }

                    if (!visited.Add(IdentityOf(current)))
                    {
                        continue;           // 走过的目录，自指向的链接就在这里被挡掉
                    }

                    foreach (var file in current.EnumerateFiles("*.pdf"))
                    {
                        found.Add(file.FullName);
                    }

                    foreach (var sub in current.EnumerateDirectories())
                    {
                        pending.Enqueue(sub.FullName);
                    }
                }
            }
            catch (Exception ex)
            {
                // 没权限、网络盘断了都走这里；已枚举到的部分仍然有效
                return (found, ex.Message);
            }

            return (found, null);
        }

        /// <summary>目录的身份：链接按它最终指向的地方算，普通目录按全路径，用来打破软链成环。</summary>
        private static string IdentityOf(DirectoryInfo info)
        {
            var raw = info.FullName;
            try
            {
                // DirectoryInfo 上没有 TargetPath，用 ResolveLinkTarget 取最终目标
                if (info.ResolveLinkTarget(returnFinalTarget: true) is FileSystemInfo target)
                {
                    raw = target.FullName;
                }
            }
            catch (Exception)
            {
                // 取不到目标就用路径本身，最坏是多算一次而不会漏
            }

            try
            {
                return Path.TrimEndingDirectorySeparator(Path.GetFullPath(raw));
            }
            catch (Exception)
            {
                return Path.TrimEndingDirectorySeparator(raw);
            }
        }

        private static bool UnderAny(string path, IReadOnlyList<string>? roots)
        {
            if (roots == null || roots.Count == 0)
            {
                return false;
            }

            var full = Full(path);
            if (full == null)
            {
                return false;
            }

            foreach (var root in roots)
            {
                var prefix = Full(root);
                if (string.IsNullOrEmpty(prefix))
                {
                    continue;
                }

                var boundary = prefix.EndsWith(Path.DirectorySeparatorChar) ? prefix : prefix + Path.DirectorySeparatorChar;
                if (full.StartsWith(boundary, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            return false;
        }

        private static string? Full(string path)
        {
            try
            {
                return Path.GetFullPath(path);
            }
            catch (Exception)
            {
                return null;            // 畸形路径（非法字符、过长）不参与判断
            }
        }
    }
}

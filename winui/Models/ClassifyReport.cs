using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace winui.Models
{
    /// <summary>与 classify_invoices.py 的 CATEGORIES 保持一致，顺序也是归档顺序。</summary>
    public static class InvoiceCategories
    {
        public const string Train = "火车票";
        public const string Vat = "增值税发票";
        public const string Regular = "普通发票";
        public const string Other = "其他发票";

        public static readonly string[] All = { Train, Vat, Regular, Other };
    }

    /// <summary>扫描到的单个 PDF。未归档的文件 Category 为空，原因写在 Note。</summary>
    public sealed record InvoiceFileEntry
    {
        /// <summary>相对所选目录的路径</summary>
        [JsonPropertyName("file")]
        public string? File { get; init; }

        [JsonPropertyName("category")]
        public string? Category { get; init; }

        [JsonPropertyName("is_multi_page")]
        public bool IsMultiPage { get; init; }

        [JsonPropertyName("note")]
        public string? Note { get; init; }

        /// <summary>这一张票的票面字段；未归档的条目没有它，服务侧只在 with_fields 时填。</summary>
        [JsonPropertyName("fields")]
        public InvoiceFields? Fields { get; init; }

        public string Display => File ?? string.Empty;

        // ListView 的模板里绑不了 bool，先折算成要显示的文字
        public string PageNote => IsMultiPage ? "多页文件" : string.Empty;
    }

    /// <summary>一次扫描的完整结果，字段对齐 collect() 的返回值。</summary>
    public sealed record ClassifyReport
    {
        [JsonPropertyName("out_dir")]
        public string? OutDir { get; init; }

        [JsonPropertyName("multi_page")]
        public int MultiPage { get; init; }

        /// <summary>各类别数量，键是中文类别名</summary>
        [JsonPropertyName("counts")]
        public Dictionary<string, int>? Counts { get; init; }

        [JsonPropertyName("non_invoices")]
        public int NonInvoices { get; init; }

        /// <summary>本来就是归档目录里的副本，按约定不再复制第二遍</summary>
        [JsonPropertyName("already_archived")]
        public int AlreadyArchived { get; init; }

        [JsonPropertyName("unreadable")]
        public int Unreadable { get; init; }

        [JsonPropertyName("files")]
        public List<InvoiceFileEntry>? Files { get; init; }

        [JsonPropertyName("log")]
        public List<string>? Log { get; init; }

        /// <summary>解析过程中攒下的票面告警，见 invoice_service.py 的 take_warnings。</summary>
        [JsonPropertyName("warnings")]
        public List<string>? Warnings { get; init; }

        /// <summary>告警总条数，可能大于 Warnings 的条数（服务侧截断到 MAX_WARNINGS）。</summary>
        [JsonPropertyName("warning_count")]
        public int WarningCount { get; init; }
    }

    /// <summary>WebSocket 推送的信封：{"kind":"log","line":"…"} 或 {"kind":"done","report":{…}}。</summary>
    public sealed record WsPush
    {
        [JsonPropertyName("kind")]
        public string? Kind { get; init; }

        [JsonPropertyName("line")]
        public string? Line { get; init; }

        [JsonPropertyName("report")]
        public ClassifyReport? Report { get; init; }
    }
}

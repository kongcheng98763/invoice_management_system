using System.Text.Json.Serialization;

namespace winui.Models
{
    /// <summary>报销总述的一行（B/C/D/F/H/I/J/K/L 列）。</summary>
    public sealed record ReimbursementRow
    {
        /// <summary>A 列序列，服务侧的主键；页面用它定位行</summary>
        [JsonPropertyName("id")]
        public int Id { get; init; }

        /// <summary>B 经手时间，服务侧已归一成 YYYY-MM-DD</summary>
        [JsonPropertyName("happened_on")]
        public string? HappenedOn { get; init; }

        /// <summary>C 报销大类</summary>
        [JsonPropertyName("category")]
        public string? Category { get; init; }

        /// <summary>D 报销项目</summary>
        [JsonPropertyName("item")]
        public string? Item { get; init; }

        /// <summary>F 金额：约定服务侧发纯数值，票面式注记走备注列而不是塞进金额里</summary>
        [JsonPropertyName("amount")]
        public decimal? Amount { get; init; }

        /// <summary>H 报销单号，历史数据里混有 "/"，故按字符串收</summary>
        [JsonPropertyName("form_no")]
        public string? FormNo { get; init; }

        /// <summary>I 项目负责人</summary>
        [JsonPropertyName("owner")]
        public string? Owner { get; init; }

        [JsonPropertyName("note1")]
        public string? Note1 { get; init; }

        [JsonPropertyName("note2")]
        public string? Note2 { get; init; }

        /// <summary>L 完成：√／×／流程中</summary>
        [JsonPropertyName("status")]
        public string? Status { get; init; }
    }
}

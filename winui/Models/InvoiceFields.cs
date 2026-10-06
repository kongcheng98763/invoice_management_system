using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace winui.Models
{
    /// <summary>
    /// 票面字段。三个提取函数（火车/增值税/普通）的键合在一张 DTO 上，
    /// Python 侧失败时会提前返回半成品 dict，所以每个键都可能缺失，一律可空。
    /// </summary>
    public sealed record InvoiceFields
    {
        [JsonPropertyName("is_train")]
        public bool? IsTrain { get; init; }

        /// <summary>电子客票号，票面上是 25 位</summary>
        [JsonPropertyName("e_ticket_number")]
        public string? ETicketNumber { get; init; }

        /// <summary>客票状态：退票／补票／始发改签</summary>
        [JsonPropertyName("state")]
        public string? State { get; init; }

        [JsonPropertyName("invoice_no")]
        public string? InvoiceNo { get; init; }

        /// <summary>开票日期／行程日期，形如 2026年04月17日，原样透传不做解析</summary>
        [JsonPropertyName("invoice_date")]
        public string? InvoiceDate { get; init; }

        [JsonPropertyName("invoice_time")]
        public string? InvoiceTime { get; init; }

        /// <summary>价税合计（小写），Python 侧已去掉票面的 ¥ 前缀，只剩纯数字，显示时由界面补</summary>
        [JsonPropertyName("total_amount")]
        public string? TotalAmount { get; init; }

        /// <summary>脱敏身份证号，形如 1234567890****123X</summary>
        [JsonPropertyName("id")]
        public string? Id { get; init; }

        [JsonPropertyName("person")]
        public string? Person { get; init; }

        /// <summary>[起点站, 终点站]</summary>
        [JsonPropertyName("site")]
        public List<string?>? Site { get; init; }

        [JsonPropertyName("is_regular")]
        public bool? IsRegular { get; init; }

        [JsonPropertyName("invoice_number")]
        public string? InvoiceNumber { get; init; }

        [JsonPropertyName("is_vat")]
        public bool? IsVat { get; init; }
    }
}

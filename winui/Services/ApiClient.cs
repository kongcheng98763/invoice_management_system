using System;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace winui.Services
{
    /// <summary>
    /// 与 Python 侧常驻 FastAPI 通信的极薄一层：只管地址拼接和 JSON 收发，不含业务。
    /// </summary>
    public sealed class ApiClient
    {
        // 端口和端点与 invoice_service.py 对齐（/health、/invoices/classify、/ws），改的时候只改这一处
        public const string DefaultHost = "127.0.0.1";
        public const int DefaultPort = 8765;

        public static string DefaultBaseUrl => $"http://{DefaultHost}:{DefaultPort}";
        public static string DefaultWsUrl => $"ws://{DefaultHost}:{DefaultPort}/ws";

        public const string HealthPath = "health";
        public const string ReimbursementsPath = "reimbursements";
        public const string ClassifyPath = "invoices/classify";
        public const string ClassifyResultPath = "invoices/classify/result";

        private static readonly HttpClient Http = new HttpClient
        {
            // 逐个 PDF 解析要多久有实测数：本机 60 份真实票样 5.6 秒，约 94 ms/份，
            // 单次上限 2000 份满批约 3 分钟。给到 20 分钟是留出多页大票表那种几倍余量，
            // 免得满批把「还在正常跑」报成超时失败
            Timeout = TimeSpan.FromMinutes(20)
        };

        private static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions(JsonSerializerDefaults.Web);

        public static ApiClient Current { get; } = new ApiClient();

        private ApiClient()
        {
        }

        public string BaseUrl { get; set; } = LocalStore.Read(LocalStore.ApiBaseUrlKey, DefaultBaseUrl);

        public Uri Url(string path)
        {
            // 地址在设置页可以手改，写成非法值时按通信失败报出去，别把格式异常抛到界面
            if (!Uri.TryCreate(BaseUrl.TrimEnd('/') + "/", UriKind.Absolute, out var baseUri))
            {
                throw new HttpRequestException($"服务地址无效：{BaseUrl}");
            }

            return new Uri(baseUri, path.TrimStart('/'));
        }

        public async Task<T?> GetAsync<T>(string path, CancellationToken cancellationToken = default)
        {
            var json = await GetRawAsync(path, cancellationToken).ConfigureAwait(false);
            return JsonSerializer.Deserialize<T>(json, JsonOptions);
        }

        public async Task<T?> PostAsync<T>(string path, object? payload, CancellationToken cancellationToken = default)
        {
            using var content = new StringContent(
                JsonSerializer.Serialize(payload, JsonOptions), Encoding.UTF8, "application/json");
            using var response = await Http.PostAsync(Url(path), content, cancellationToken).ConfigureAwait(false);
            var json = await ReadBodyAsync(response, cancellationToken).ConfigureAwait(false);
            return JsonSerializer.Deserialize<T>(json, JsonOptions);
        }

        public async Task<string> GetRawAsync(string path, CancellationToken cancellationToken = default)
        {
            using var response = await Http.GetAsync(Url(path), cancellationToken).ConfigureAwait(false);
            return await ReadBodyAsync(response, cancellationToken).ConfigureAwait(false);
        }

        /// <summary>非 2xx 时把状态码原样抛出去，让调用方能分清「服务没起来」和「这个端点还没实现」。</summary>
        private static async Task<string> ReadBodyAsync(HttpResponseMessage response, CancellationToken cancellationToken)
        {
            var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                throw new ApiStatusException(response.StatusCode, body);
            }

            return body;
        }
    }

    /// <summary>本地服务可达但返回了错误状态码；StatusCode 沿用 HttpRequestException 上的，Message 里带上 FastAPI 的 detail 片段。</summary>
    public sealed class ApiStatusException : HttpRequestException
    {
        public ApiStatusException(HttpStatusCode statusCode, string body)
            : base($"服务返回 {(int)statusCode} {statusCode}{DetailOf(body)}", null, statusCode)
        {
        }

        private static string DetailOf(string body)
        {
            if (string.IsNullOrWhiteSpace(body))
            {
                return string.Empty;
            }

            var oneLine = body.Replace("\r", " ").Replace("\n", " ").Trim();
            return "：" + (oneLine.Length <= 160 ? oneLine : oneLine.Substring(0, 160) + "…");
        }
    }
}

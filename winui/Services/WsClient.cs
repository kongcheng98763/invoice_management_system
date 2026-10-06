using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace winui.Services
{
    /// <summary>
    /// 连到 Python 服务的 WebSocket，收到一条完整消息就往外抛一次事件。
    /// 回调发生在后台线程，界面侧要自己回投递到 UI 线程。
    /// </summary>
    public sealed class WsClient : IDisposable
    {
        public static WsClient Current { get; } = new WsClient();

        public event Action<string>? MessageReceived;
        public event Action<string>? StateChanged;

        private readonly SemaphoreSlim _sendLock = new SemaphoreSlim(1, 1);
        private ClientWebSocket? _socket;
        private CancellationTokenSource? _cts;

        public string Url { get; set; } = LocalStore.Read(LocalStore.WsUrlKey, ApiClient.DefaultWsUrl);

        public bool IsConnected => _socket is { State: WebSocketState.Open };

        public async Task ConnectAsync()
        {
            await DisconnectAsync().ConfigureAwait(false);

            var socket = new ClientWebSocket();
            var cts = new CancellationTokenSource();
            try
            {
                await socket.ConnectAsync(new Uri(Url), cts.Token).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // 服务还没起来是常态，这里不重连，只把资源收干净让调用方决定怎么提示
                socket.Dispose();
                cts.Dispose();
                StateChanged?.Invoke("未连接");
                throw;
            }

            _socket = socket;
            _cts = cts;
            StateChanged?.Invoke("已连接");
            _ = ReceiveLoopAsync(socket, cts.Token);
        }

        public async Task SendAsync(string text, CancellationToken cancellationToken = default)
        {
            var socket = _socket;
            if (socket == null || socket.State != WebSocketState.Open)
            {
                throw new InvalidOperationException("WebSocket 尚未连接");
            }

            var bytes = Encoding.UTF8.GetBytes(text);
            await _sendLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                await socket.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cancellationToken)
                    .ConfigureAwait(false);
            }
            finally
            {
                _sendLock.Release();
            }
        }

        public async Task DisconnectAsync()
        {
            var socket = _socket;
            var cts = _cts;
            _socket = null;
            _cts = null;

            if (cts != null)
            {
                cts.Cancel();
                cts.Dispose();
            }

            if (socket != null)
            {
                try
                {
                    if (socket.State == WebSocketState.Open || socket.State == WebSocketState.CloseReceived)
                    {
                        await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "客户端主动关闭", CancellationToken.None)
                            .ConfigureAwait(false);
                    }
                }
                catch (WebSocketException)
                {
                }
                catch (ObjectDisposedException)
                {
                }
                catch (OperationCanceledException)
                {
                }
                finally
                {
                    socket.Dispose();
                }
            }

            StateChanged?.Invoke("未连接");
        }

        private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken cancellationToken)
        {
            var buffer = new byte[8192];
            var chars = new char[Encoding.UTF8.GetMaxCharCount(buffer.Length)];
            // 一个汉字可能被拆在两帧里：按帧 GetString 会各自补一个 U+FFFD，
            // 大报文（几千条明细）全是中文，必然踩到，严重时 JSON 直接解析失败
            var decoder = Encoding.UTF8.GetDecoder();
            var text = new StringBuilder();
            try
            {
                while (!cancellationToken.IsCancellationRequested && socket.State == WebSocketState.Open)
                {
                    var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), cancellationToken)
                        .ConfigureAwait(false);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        break;
                    }

                    text.Append(chars, 0, decoder.GetChars(buffer, 0, result.Count, chars, 0, result.EndOfMessage));
                    if (result.EndOfMessage)
                    {
                        var message = text.ToString();
                        text.Clear();
                        // 处理器里抛出来的异常跟连接本身没关系，不能让它把接收循环带走：
                        // 循环一死既不报错也不置空 _socket，界面会以为还连着
                        try
                        {
                            MessageReceived?.Invoke(message);
                        }
                        catch (Exception)
                        {
                        }
                    }
                }
            }
            catch (OperationCanceledException)
            {
            }
            catch (WebSocketException)
            {
            }
            catch (ObjectDisposedException)
            {
            }
            finally
            {
                // 断线后不自动重连（带退避的重连待做）：发票识别页每次进来都会自己补连一次
                if (_socket == socket)
                {
                    _socket = null;
                    StateChanged?.Invoke("已断开");
                }
            }
        }

        public void Dispose()
        {
            _cts?.Cancel();
            _cts?.Dispose();
            _cts = null;
            _socket?.Dispose();
            _socket = null;
        }
    }
}

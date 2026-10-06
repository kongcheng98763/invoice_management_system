using System;
using System.IO;

namespace winui.Services
{
    /// <summary>
    /// 只存几个字符串偏好的落盘文件，一行一个值。
    /// 不做登录也不做配置系统，够用即可。
    /// </summary>
    public static class LocalStore
    {
        public const string LastPageKey = "last_page";
        public const string RestoreLastPageKey = "restore_last_page";
        public const string ApiBaseUrlKey = "api_base_url";
        public const string WsUrlKey = "ws_url";
        public const string ArchiveDirKey = "archive_dir";
        public const string SourceFolderKey = "source_folder";

        private static readonly string Root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "invoice_management_system");

        public static string Read(string key, string fallback)
        {
            try
            {
                var file = Path.Combine(Root, key + ".txt");
                if (!File.Exists(file))
                {
                    return fallback;
                }
                var text = File.ReadAllText(file).Trim();
                return text.Length == 0 ? fallback : text;
            }
            catch (IOException)
            {
                return fallback;
            }
            catch (UnauthorizedAccessException)
            {
                return fallback;
            }
        }

        /// <summary>落盘；返回 false 表示没写进去（磁盘满、目录被 ACL 挡住），调用方要告诉用户。</summary>
        public static bool Write(string key, string value)
        {
            try
            {
                Directory.CreateDirectory(Root);
                File.WriteAllText(Path.Combine(Root, key + ".txt"), value);
                return true;
            }
            catch (IOException)
            {
                return false;
            }
            catch (UnauthorizedAccessException)
            {
                return false;
            }
        }

        public static bool ReadBool(string key, bool fallback)
        {
            var text = Read(key, fallback ? "1" : "0");
            return text == "1" || string.Equals(text, "true", StringComparison.OrdinalIgnoreCase);
        }

        public static bool Write(string key, bool value) => Write(key, value ? "1" : "0");
    }
}

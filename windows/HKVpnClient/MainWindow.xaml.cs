using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Windows;

namespace HKVpnClient;

public partial class MainWindow : Window
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private readonly string _dataDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "HK VPN");
    private string ConfigPath => Path.Combine(_dataDirectory, "HK-VPN.conf");
    private bool _connected;

    public MainWindow()
    {
        InitializeComponent();
        EndpointBox.Text = Properties.Settings.Default.Endpoint;
    }

    private async void ConnectButton_Click(object sender, RoutedEventArgs e)
    {
        ConnectButton.IsEnabled = false;
        try
        {
            if (_connected) { Disconnect(); return; }
            var config = File.Exists(ConfigPath) ? await File.ReadAllTextAsync(ConfigPath) : await EnrollAsync();
            await File.WriteAllTextAsync(ConfigPath, config);
            InstallTunnel();
            _connected = true; RenderState();
        }
        catch (Exception error) { MessageBox.Show(error.Message, "HK VPN", MessageBoxButton.OK, MessageBoxImage.Error); }
        finally { ConnectButton.IsEnabled = true; }
    }

    private async Task<string> EnrollAsync()
    {
        var endpoint = EndpointBox.Text.Trim().TrimEnd('/');
        if (!endpoint.StartsWith("https://", StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("控制台地址必须以 https:// 开头。");
        if (string.IsNullOrWhiteSpace(PasswordBox.Password)) throw new InvalidOperationException("请输入管理密码。");
        var clientId = Properties.Settings.Default.ClientId;
        if (string.IsNullOrEmpty(clientId)) { clientId = Guid.NewGuid().ToString("N"); Properties.Settings.Default.ClientId = clientId; }
        var json = JsonSerializer.Serialize(new { password = PasswordBox.Password, clientId, platform = "Windows" });
        using var response = await _http.PostAsync(endpoint + "/api/client/enroll", new StringContent(json, Encoding.UTF8, "application/json"));
        var body = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException(JsonDocument.Parse(body).RootElement.TryGetProperty("error", out var e) ? e.GetString() : "服务器拒绝请求");
        Properties.Settings.Default.Endpoint = endpoint; Properties.Settings.Default.Save();
        return JsonDocument.Parse(body).RootElement.GetProperty("wireGuardConfig").GetString() ?? throw new InvalidOperationException("服务器未返回配置");
    }

    private void InstallTunnel()
    {
        Directory.CreateDirectory(_dataDirectory);
        var executable = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "WireGuard", "wireguard.exe");
        if (!File.Exists(executable)) throw new InvalidOperationException("请先安装官方 WireGuard for Windows，然后再次点击 Connect。\nhttps://www.wireguard.com/install/");
        Run(executable, $"/installtunnelservice \"{ConfigPath}\"");
    }

    private void Disconnect()
    {
        var executable = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "WireGuard", "wireguard.exe");
        if (File.Exists(executable)) Run(executable, "/uninstalltunnelservice HK-VPN");
        _connected = false; RenderState();
    }

    private static void Run(string file, string arguments)
    {
        using var process = Process.Start(new ProcessStartInfo(file, arguments) { UseShellExecute = true, Verb = "runas" });
        process?.WaitForExit();
        if (process?.ExitCode != 0) throw new InvalidOperationException("WireGuard 命令执行失败。");
    }

    private void RenderState() { StatusText.Text = _connected ? "已连接到 Hong Kong" : "未连接"; ConnectButton.Content = _connected ? "Disconnect" : "Connect"; }
    private void Ikev2Button_Click(object sender, RoutedEventArgs e) => MessageBox.Show("系统设置备用：打开浏览器控制台，在对应设备中复制 IKEv2 服务器、用户名与密码；然后在 Windows 设置 → 网络和 Internet → VPN 中添加 IKEv2 连接。", "IKEv2 备用方式");
}

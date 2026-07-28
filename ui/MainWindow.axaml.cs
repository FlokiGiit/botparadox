// Fenêtre de pilotage du bot.
//
// Elle ne décode rien elle-même : le bot Python reste la seule source de
// vérité. L'interface se contente de lancer le processus et d'interroger le
// point /stats qu'il expose déjà. Toute la logique de jeu reste donc là où
// elle est testée, et l'affichage peut évoluer sans y toucher.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;

namespace ui;

// Types explicites plutôt qu'anonymes : les liaisons compilées d'Avalonia ont
// besoin de connaître la forme des données à la compilation.
public record JobRow(string? Name, string Detail, double Pct);
public record ItemRow(string? Name, string Gained, Bitmap? Icon);
public record EventRow(string Time, string? Text, IBrush Colour, Bitmap? Icon);
public record LogRow(string Time, string? Text);

public partial class MainWindow : Window
{
    const string StatsUrl = "http://127.0.0.1:8765/stats";
    const string DashboardUrl = "http://127.0.0.1:8765/";
    const string ShutdownUrl = "http://127.0.0.1:8765/shutdown";
    const string PauseUrl = "http://127.0.0.1:8765/pause";
    const string ResumeUrl = "http://127.0.0.1:8765/resume";
    const string ModeUrl = "http://127.0.0.1:8765/mode/";

    // Les icônes sont lues directement dans les ressources du client plutôt
    // que servies par HTTP : c'est le même disque, autant éviter le détour.
    // Le client vit hors du projet et une mise à jour le déplace (Paradox ->
    // Nexus). On teste donc les emplacements connus, le plus récent d'abord,
    // en miroir de client_config.py côté Python.
    static readonly string IconRoot = ResolveIconRoot();

    // Dossier de l'exe réellement lancé. En single-file, AppContext.BaseDirectory
    // peut pointer le dossier d'extraction temporaire du runtime : Environment
    // .ProcessPath donne le vrai emplacement de BotParadox.exe.
    static string ExeDir()
    {
        var p = Environment.ProcessPath;
        return p is not null ? Path.GetDirectoryName(p)! : AppContext.BaseDirectory;
    }

    // Emplacement canonique où l'installateur dépose l'appli.
    static string InstallDir() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "BotParadox");

    static string ResolveIconRoot()
    {
        // 1. Pointeur écrit par le bot à chaque lancement : le chemin retroclient
        //    déjà résolu (mode installé : botcore\data ; mode dev : projet\data).
        foreach (var ptr in new[]
        {
            Path.Combine(ExeDir(), "botcore", "data", "client_path.txt"),
            Path.Combine(InstallDir(), "botcore", "data", "client_path.txt"),
            Path.Combine(ProjectDir(), "data", "client_path.txt"),
        })
        {
            try
            {
                if (File.Exists(ptr))
                {
                    var root = Path.Combine(File.ReadAllText(ptr).Trim(), "clips", "items");
                    if (Directory.Exists(root)) return root;
                }
            }
            catch { /* pointeur illisible : on tente la détection */ }
        }

        // 2. Détection par emplacements connus, valable quel que soit l'utilisateur.
        string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string appdata = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string[] apps =
        {
            Path.Combine(desktop, "Nexus", "srv_nexus", "resources", "app"),
            Path.Combine(home, "Nexus", "srv_nexus", "resources", "app"),
            Path.Combine(appdata, "Paradox", "resources", "app"),
        };
        foreach (var app in apps)
        {
            var root = Path.Combine(app, "retroclient", "clips", "items");
            if (Directory.Exists(root)) return root;
        }
        return Path.Combine(apps[0], "retroclient", "clips", "items");
    }

    readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(3) };
    readonly Dictionary<string, Bitmap?> _icons = new();
    readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(1.5) };
    Process? _bot;

    public MainWindow()
    {
        InitializeComponent();
        _timer.Tick += async (_, _) => await Refresh();
        _timer.Start();
        Opened += async (_, _) =>
        {
            // Le pont ne redirige que si le proxy ecoute deja au moment ou le
            // joueur choisit son serveur. Demarrer d'office supprime tout
            // risque de lancer les choses dans le mauvais ordre.
            if (!await IsAlive()) StartBot();
            _ = CheckUpdateAsync();   // en tache de fond, sans bloquer l'UI
        };

    }

    // ── mises à jour (GitHub Releases) ───────────────────────────────────────
    // Le repo et la version courante sont livrés à côté de l'exe (update.json,
    // version.txt), écrits par build_installer.py. Au lancement on interroge la
    // dernière release ; si sa version est plus récente, on propose de télécharger
    // le nouveau Setup et de le lancer (il remplace l'installation).
    string? _setupUrl;

    static string? ReadSideFile(string name)
    {
        foreach (var dir in new[] { ExeDir(), InstallDir() })
        {
            try
            {
                var p = Path.Combine(dir, name);
                if (File.Exists(p)) return File.ReadAllText(p).Trim();
            }
            catch { }
        }
        return null;
    }

    static string? UpdateRepo()
    {
        var j = ReadSideFile("update.json");
        if (j is null) return null;
        try { using var d = JsonDocument.Parse(j); return d.RootElement.GetProperty("repo").GetString(); }
        catch { return null; }
    }

    static Version LocalVersion()
    {
        var v = (ReadSideFile("version.txt") ?? "0.0.0").TrimStart('v', 'V');
        return Version.TryParse(v, out var ver) ? ver : new Version(0, 0, 0);
    }

    async Task CheckUpdateAsync()
    {
        var repo = UpdateRepo();
        // repo absent ou gabarit non renseigné : pas de vérification.
        if (string.IsNullOrWhiteSpace(repo) || repo!.StartsWith("OWNER/")) return;
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("BotParadox-Updater");
            var json = await http.GetStringAsync(
                $"https://api.github.com/repos/{repo}/releases/latest");
            using var doc = JsonDocument.Parse(json);
            var tag = doc.RootElement.GetProperty("tag_name").GetString() ?? "";
            if (!Version.TryParse(tag.TrimStart('v', 'V'), out var remote)) return;
            if (remote <= LocalVersion()) return;

            foreach (var a in doc.RootElement.GetProperty("assets").EnumerateArray())
            {
                var name = a.GetProperty("name").GetString() ?? "";
                if (name.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                {
                    _setupUrl = a.GetProperty("browser_download_url").GetString();
                    break;
                }
            }
            if (_setupUrl is not null)
                Dispatcher.UIThread.Post(() =>
                {
                    UpdateBtn.Content = $"Mettre à jour → {tag}";
                    UpdateBtn.IsVisible = true;
                });
        }
        catch { /* hors ligne ou release indisponible : on ignore */ }
    }

    async void OnUpdate(object? sender, RoutedEventArgs e)
    {
        if (_setupUrl is null) return;
        UpdateBtn.IsEnabled = false;
        UpdateBtn.Content = "Téléchargement…";
        try
        {
            var tmp = Path.Combine(Path.GetTempPath(), "BotParadox-Setup.exe");
            using (var http = new HttpClient { Timeout = TimeSpan.FromMinutes(5) })
            using (var s = await http.GetStreamAsync(_setupUrl))
            using (var f = File.Create(tmp))
                await s.CopyToAsync(f);
            // L'installateur ferme les instances en cours, remplace les fichiers,
            // ré-applique les patchs et relance. On se contente de le lancer et
            // de quitter pour libérer nos propres fichiers.
            Process.Start(new ProcessStartInfo(tmp) { UseShellExecute = true });
            Close();
        }
        catch (Exception ex)
        {
            UpdateBtn.Content = "Échec MAJ";
            UpdateBtn.IsEnabled = true;
            Status.Text = ex.Message;
        }
    }

    // ── cycle de vie du bot ──────────────────────────────────────────────────

    static string ProjectDir()
    {
        // L'exe est enfoui sous bin/Debug/netX ; on remonte jusqu'au dossier
        // qui contient bot.py plutôt que de coder un nombre de niveaux en dur.
        var dir = new DirectoryInfo(ExeDir());
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "bot.py")))
            dir = dir.Parent;
        return dir?.FullName ?? ExeDir();
    }

    bool _running;
    bool _enabled = true;

    // Mettre en pause n'interrompt pas la connexion de jeu : le proxy la
    // porte, et le tuer obligerait à relancer Paradox depuis l'écran de
    // connexion. Seul « Arrêter le bot » coupe vraiment.
    async void OnStartStop(object? sender, RoutedEventArgs e)
    {
        if (!_running) { StartBot(); return; }
        try
        {
            await _http.GetStringAsync(_enabled ? PauseUrl : ResumeUrl);
        }
        catch { /* le prochain rafraîchissement rétablira l'affichage */ }
    }

    async void OnShutdown(object? sender, RoutedEventArgs e) => await StopBot();

    // Le mode vit dans le bot, pas dans la fenetre : il survit donc a la
    // fermeture de l'interface et reste coherent avec le tableau de bord web.
    async void OnObserve(object? sender, RoutedEventArgs e) => await SetMode("off");
    async void OnHarvest(object? sender, RoutedEventArgs e) => await SetMode("harvest");
    async void OnFarm(object? sender, RoutedEventArgs e) => await SetMode("farm");

    async Task SetMode(string mode)
    {
        try { await _http.GetStringAsync(ModeUrl + mode); } catch { }
    }

    async Task<bool> IsAlive()
    {
        try { await _http.GetStringAsync(StatsUrl); return true; }
        catch { return false; }
    }

    // Bot figé livré à côté de l'UI (mode installé), sinon null (mode dev).
    // On regarde à côté de l'exe puis à l'emplacement d'install canonique : en
    // single-file, le dossier de l'exe peut être mal résolu selon le runtime.
    static string? BotCoreExe()
    {
        foreach (var cand in new[]
        {
            Path.Combine(ExeDir(), "botcore", "botcore.exe"),
            Path.Combine(InstallDir(), "botcore", "botcore.exe"),
        })
            if (File.Exists(cand)) return cand;
        return null;
    }

    void StartBot()
    {
        ProcessStartInfo psi;
        var exe = BotCoreExe();
        if (exe is not null)
        {
            // Installé : on lance le bot figé, aucune dépendance requise.
            psi = new ProcessStartInfo
            {
                FileName = exe,
                WorkingDirectory = Path.GetDirectoryName(exe)!,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
        }
        else
        {
            // Développement : on lance les sources avec Python.
            var dir = ProjectDir();
            if (!File.Exists(Path.Combine(dir, "bot.py")))
            {
                Status.Text = "botcore introuvable sous " + InstallDir();
                return;
            }
            psi = new ProcessStartInfo
            {
                FileName = "python",
                Arguments = "bot.py",
                WorkingDirectory = dir,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
        }

        try
        {
            _bot = Process.Start(psi);
            _running = true;
            StartStop.Content = "Arrêter";
        }
        catch (Exception ex)
        {
            Status.Text = $"lancement impossible : {ex.Message}";
        }
    }

    async Task StopBot()
    {
        // On demande d'abord l'arrêt par le réseau : ça fonctionne même si le
        // bot a été lancé en dehors de cette fenêtre, ce qui n'était pas le
        // cas quand on se contentait de tuer le processus qu'on avait ouvert.
        try { await _http.GetStringAsync(ShutdownUrl); } catch { /* déjà coupé */ }

        try
        {
            if (_bot is { HasExited: false })
                _bot.Kill(entireProcessTree: true);
        }
        catch { /* déjà mort : rien à faire */ }

        _bot = null;
        _running = false;
        StartStop.Content = "Démarrer";
        Shutdown.IsEnabled = false;
        Status.Text = "arrêté";
        Dot.Fill = new SolidColorBrush(Color.Parse("#e05561"));
    }

    // ── rafraîchissement ─────────────────────────────────────────────────────

    async Task Refresh()
    {
        JsonElement root;
        try
        {
            root = JsonDocument.Parse(await _http.GetStringAsync(StatsUrl)).RootElement;
        }
        catch
        {
            Dot.Fill = new SolidColorBrush(Color.Parse("#e05561"));
            Status.Text = _bot is { HasExited: false } ? "démarrage…" : "arrêté";
            _running = _bot is { HasExited: false };
            StartStop.Content = _running ? "Mettre en pause" : "Démarrer";
            Shutdown.IsEnabled = _running;
            return;
        }

        _running = true;                 // qu'il vienne d'ici ou d'un terminal
        _enabled = root.GetProperty("enabled").GetBoolean();
        StartStop.Content = _enabled ? "Mettre en pause" : "Reprendre";
        var client = root.GetProperty("client").GetBoolean();
        Status.Text = !client ? "prêt — lance Paradox et connecte-toi"
                    : _enabled ? "en marche" : "en pause";
        Dot.Fill = new SolidColorBrush(Color.Parse(
            !client ? "#4a9eff" : _enabled ? "#7dd3a0" : "#ffb454"));
        Shutdown.IsEnabled = true;

        var mode = root.GetProperty("mode").GetString() ?? "harvest";
        var farming = mode == "farm";
        CardKills.IsVisible = farming;
        CardXp.IsVisible = farming;
        CardHarvests.IsVisible = !farming;
        CardPods.IsVisible = !farming;
        // Les metiers ne progressent pas en combat : hors sujet en farm.
        JobsTitle.IsVisible = !farming;
        Jobs.IsVisible = !farming;
        var off = mode == "off";
        TabObserve.Background = new SolidColorBrush(Color.Parse(
            off ? "#4a9eff" : "#2a2f3a"));
        TabHarvest.Background = new SolidColorBrush(Color.Parse(
            mode == "harvest" ? "#4a9eff" : "#2a2f3a"));
        TabFarm.Background = new SolidColorBrush(Color.Parse(
            farming ? "#4a9eff" : "#2a2f3a"));

        Kills.Text = root.GetProperty("kills").GetInt32().ToString("N0");
        KillsRate.Text = $"{root.GetProperty("kills_per_hour").GetDouble():0} / heure";
        XpGained.Text = Compact(root.GetProperty("xp_gained").GetInt64());
        XpGainedRate.Text = Compact((long)root.GetProperty("xp_per_hour").GetDouble()) + " / heure";
        Kamas.Text = root.GetProperty("kamas_gained").GetInt64().ToString("N0");
        KamasRate.Text = $"{root.GetProperty("kamas_per_hour").GetDouble():N0} / heure";

        Harvests.Text = root.GetProperty("harvests").GetInt32().ToString();
        Rate.Text = $"{root.GetProperty("harvests_per_hour").GetDouble():0} / heure";
        Fights.Text = root.GetProperty("fights").GetInt32().ToString();
        Pods.Text = root.GetProperty("pods").GetInt32().ToString("N0");
        PodsBar.Value = Math.Min(100, root.GetProperty("pods_pct").GetDouble());
        Uptime.Text = TimeSpan.FromSeconds(root.GetProperty("uptime").GetDouble())
                              .ToString(@"h\:mm\:ss");

        var map = root.GetProperty("map_id");
        MapId.Text = map.ValueKind == JsonValueKind.Number ? $"carte {map.GetInt32()}" : "";

        var lvl = root.GetProperty("level");
        Level.Text = lvl.ValueKind == JsonValueKind.Number ? lvl.GetInt32().ToString() : "—";

        var xp = root.GetProperty("xp");
        if (xp.ValueKind == JsonValueKind.Object)
        {
            XpBar.Value = Math.Min(100, xp.GetProperty("pct").GetDouble());
            XpText.Text = $"{xp.GetProperty("pct").GetDouble():0.0}% — reste "
                        + Compact(xp.GetProperty("remaining").GetInt64());
        }

        var jobs = new List<JobRow>();
        foreach (var j in root.GetProperty("jobs").EnumerateArray())
            jobs.Add(new JobRow(
                j.GetProperty("name").GetString(),
                $"niv. {j.GetProperty("level").GetInt32()} — {j.GetProperty("pct").GetDouble():0}%",
                j.GetProperty("pct").GetDouble()));
        Jobs.ItemsSource = jobs;

        var items = new List<ItemRow>();
        foreach (var i in root.GetProperty("items").EnumerateArray())
            items.Add(new ItemRow(
                i.GetProperty("name").GetString(),
                "+" + i.GetProperty("gained").GetInt64().ToString("N0"),
                LoadIcon(i.GetProperty("gfx").GetString())));
        Items.ItemsSource = items;

        var events = new List<EventRow>();
        foreach (var e in root.GetProperty("events").EnumerateArray())
        {
            var kind = e.GetProperty("kind").GetString();
            events.Add(new EventRow(
                DateTimeOffset.FromUnixTimeSeconds(
                    (long)e.GetProperty("t").GetDouble()).LocalDateTime.ToString("HH:mm:ss"),
                e.GetProperty("text").GetString(),
                new SolidColorBrush(Color.Parse(kind switch
                {
                    "harvest" => "#7dd3a0",
                    "fight" => "#ffb454",
                    "drop" => "#4a9eff",
                    "levelup" => "#c084fc",
                    "xp" => "#7dd3a0",
                    "kamas" => "#ffd166",
                    _ => "#e6e8eb",
                })),
                e.TryGetProperty("gfx", out var g) ? LoadIcon(g.GetString()) : null));
        }
        Events.ItemsSource = events;

        var logs = new List<LogRow>();
        foreach (var l in root.GetProperty("logs").EnumerateArray())
            logs.Add(new LogRow(
                DateTimeOffset.FromUnixTimeSeconds(
                    (long)l.GetProperty("t").GetDouble()).LocalDateTime.ToString("HH:mm:ss"),
                l.GetProperty("text").GetString()));
        Logs.ItemsSource = logs;
    }

    // Les paliers d'XP se comptent en millions : les afficher en entier ne
    // tient pas dans une carte et n'apprend rien de plus.
    static string Compact(long n) => n switch
    {
        >= 1_000_000_000 => $"{n / 1_000_000_000d:0.#} Md",
        >= 1_000_000 => $"{n / 1_000_000d:0.#} M",
        >= 1_000 => $"{n / 1_000d:0.#} k",
        _ => n.ToString(),
    };

    Bitmap? LoadIcon(string? key)
    {
        if (string.IsNullOrEmpty(key)) return null;
        if (_icons.TryGetValue(key, out var cached)) return cached;

        Bitmap? bmp = null;
        try
        {
            // La clé vaut "<type>/<gfx>" : les deux segments sont nécessaires,
            // un même nom de fichier existe dans des dizaines de dossiers.
            var path = Path.Combine(IconRoot,
                key.Replace('/', Path.DirectorySeparatorChar) + ".png");
            if (File.Exists(path)) bmp = new Bitmap(path);
        }
        catch { /* icône illisible : on affichera juste le nom */ }

        _icons[key] = bmp;
        return bmp;
    }

    void OnOpenWeb(object? sender, RoutedEventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo(DashboardUrl) { UseShellExecute = true });
        }
        catch { /* pas de navigateur : sans conséquence */ }
    }
}

// Choix des sorts de combat, en natif.
//
// Comme le reste de l'interface, cette fenêtre ne décide rien : elle lit et
// écrit les réglages via les points /combat que le bot expose déjà, et que le
// tableau de bord web utilise aussi. Les deux vues restent donc d'accord, et la
// logique de combat n'a qu'une seule source de vérité.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Input;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;

namespace ui;

// Les boutons vivent dans des gabarits de liste : plutôt que de retrouver la
// ligne cliquée en fouillant l'arbre visuel, chaque ligne porte ses actions.
public sealed class Act : ICommand
{
    readonly Action _run;
    public Act(Action run) => _run = run;
    public bool CanExecute(object? p) => true;
    public void Execute(object? p) => _run();
    public event EventHandler? CanExecuteChanged { add { } remove { } }
}

// `Dim` : les sorts non appris restent visibles mais en retrait — ils comptent
// dans la priorite le jour ou le personnage les apprendra.
public record PickRow(string Rank, string? Name, string Detail, IBrush Colour,
                      double Dim, Bitmap? Icon,
                      ICommand Up, ICommand Down, ICommand Remove);
public record AvailRow(string? Name, string Detail, IBrush Colour, double Dim,
                       bool Known, Bitmap? Icon, ICommand Add);
public record BuffRow(string? Name, bool On, IBrush Colour, double Dim,
                      Bitmap? Icon, ICommand Toggle);

public partial class SpellsWindow : Window
{
    const string Root = "http://127.0.0.1:8765/combat";

    static readonly IBrush Normal = new SolidColorBrush(Color.Parse("#ECE6E5"));
    static readonly IBrush Unlearned = new SolidColorBrush(Color.Parse("#D9A85C"));

    readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(3) };

    // Les icones vivent dans le dossier data du bot. On teste les deux
    // emplacements possibles : celui du bot lance (installe : botcore\data) puis
    // celui des sources, pour que la fenetre marche aussi en developpement quand
    // c'est le bot installe qui tourne.
    static readonly string[] SpellIconDirs = MainWindow.SpellIconDirs();

    // Les curseurs émettent un évènement à chaque cran franchi : on regroupe
    // pour ne pas inonder le bot pendant le glissement.
    readonly DispatcherTimer _push = new() { Interval = TimeSpan.FromMilliseconds(400) };
    readonly Dictionary<string, string> _pending = new();

    // Écrire dans un contrôle déclenche son évènement : sans ce drapeau, peindre
    // l'état reçu renverrait aussitôt ce même état au bot.
    bool _painting;

    // Le drapeau ne suffit pas : un curseur peut réajuster sa valeur après coup
    // (mise en page, alignement sur les crans) et l'évènement arrive alors hors
    // de la peinture. On avait ainsi vu « trajet max » passer de 30 à 23 pas tout
    // seul. On ne transmet donc que ce qui diffère de la valeur affichée.
    double _shownDelay = -1;
    double _shownSteps = -1;

    JsonElement _state;
    bool _loaded;

    // Icones de sorts : PNG extraits du grimoire par spell_icons.py. Le cache
    // evite de relire le disque a chaque repeinture de la liste.
    readonly Dictionary<int, Bitmap?> _icons = new();

    Bitmap? SpellIcon(int id)
    {
        if (_icons.TryGetValue(id, out var cached)) return cached;
        Bitmap? bmp = null;
        foreach (var dir in SpellIconDirs)
        {
            try
            {
                var path = Path.Combine(dir, id + ".png");
                if (File.Exists(path)) { bmp = new Bitmap(path); break; }
            }
            catch { /* icone illisible : la ligne s'affiche sans */ }
        }
        _icons[id] = bmp;
        return bmp;
    }

    public SpellsWindow()
    {
        InitializeComponent();

        // Le filtre ne touche pas au bot : il ne change que l'affichage de la
        // colonne « disponibles », qui compte plus de 400 lignes sans lui.
        Search.TextChanged += (_, _) => Paint();

        Delay.ValueChanged += (_, _) =>
        {
            DelayText.Text = $"{Delay.Value / 100:0.00} s";
            if (_painting || (int)Delay.Value == (int)_shownDelay) return;
            _shownDelay = Delay.Value;
            Queue("delay", $"{Root}/delay/{(int)Delay.Value}");
        };
        Steps.ValueChanged += (_, _) =>
        {
            StepsText.Text = $"{(int)Steps.Value} pas";
            if (_painting || (int)Steps.Value == (int)_shownSteps) return;
            _shownSteps = Steps.Value;
            Queue("steps", $"{Root}/steps/{(int)Steps.Value}");
        };

        _push.Tick += async (_, _) =>
        {
            _push.Stop();
            var urls = new List<string>(_pending.Values);
            _pending.Clear();
            foreach (var u in urls) await Send(u);
        };

        Opened += async (_, _) => await Send(Root);
    }

    void Queue(string key, string url)
    {
        _pending[key] = url;
        _push.Stop();
        _push.Start();
    }

    // Chaque commande renvoie l'état à jour : un seul aller-retour suffit pour
    // agir et se réafficher, sans risque d'afficher un état périmé.
    async Task Send(string url)
    {
        try
        {
            _state = JsonDocument.Parse(await _http.GetStringAsync(url)).RootElement;
            _loaded = true;
        }
        catch
        {
            _loaded = false;
            Warn.Text = "Bot injoignable : démarre-le depuis la fenêtre principale.";
            Warn.IsVisible = true;
            return;
        }
        Paint();
    }

    static string Detail(JsonElement s)
    {
        var bits = new List<string>
        {
            $"{s.GetProperty("pa").GetInt32()} PA",
            $"portée {s.GetProperty("rmin").GetInt32()}-{s.GetProperty("rmax").GetInt32()}",
        };
        var zone = s.GetProperty("zone").GetInt32();
        if (zone > 0) bits.Add($"zone {zone}");
        var max = s.GetProperty("max").GetInt32();
        if (max > 0) bits.Add($"{max}/tour");
        if (!s.GetProperty("learned").GetBoolean()) bits.Add("non appris");
        return string.Join(" · ", bits);
    }

    void Paint()
    {
        if (!_loaded) return;
        _painting = true;

        Warn.IsVisible = !_state.GetProperty("known").GetBoolean();
        Warn.Text = "Sorts non confirmés par le serveur : connecte-toi pour que la "
                  + "liste corresponde à la classe du personnage. En attendant, le "
                  + "bot n'utilisera que les sorts qu'il possède réellement.";

        var by = new Dictionary<int, JsonElement>();
        foreach (var s in _state.GetProperty("spells").EnumerateArray())
            by[s.GetProperty("id").GetInt32()] = s;

        var selected = new List<int>();
        foreach (var id in _state.GetProperty("selected").EnumerateArray())
            selected.Add(id.GetInt32());

        var picked = new List<PickRow>();
        foreach (var id in selected)
        {
            if (!by.TryGetValue(id, out var s)) continue;   // sort d'une autre classe
            var sid = id;
            var known = s.GetProperty("learned").GetBoolean();
            picked.Add(new PickRow(
                $"{picked.Count + 1}",
                s.GetProperty("name").GetString(),
                Detail(s),
                known ? Normal : Unlearned,
                known ? 1.0 : 0.6,
                SpellIcon(sid),
                new Act(() => Fire($"{Root}/order/{sid}/up")),
                new Act(() => Fire($"{Root}/order/{sid}/down")),
                new Act(() => Fire($"{Root}/toggle/{sid}"))));
        }
        Picked.ItemsSource = picked;
        EmptyPick.IsVisible = picked.Count == 0;

        var avail = new List<AvailRow>();
        var buffs = new List<BuffRow>();
        var onBuff = new HashSet<int>();
        foreach (var id in _state.GetProperty("buffs").EnumerateArray())
            onBuff.Add(id.GetInt32());

        var needle = (Search.Text ?? "").Trim();
        var confirmed = _state.GetProperty("known").GetBoolean();
        var learnedOnly = OnlyLearned.IsChecked == true;
        var total = 0;

        foreach (var s in _state.GetProperty("spells").EnumerateArray())
        {
            var sid = s.GetProperty("id").GetInt32();
            var name = s.GetProperty("name").GetString();
            var known = s.GetProperty("learned").GetBoolean();
            var colour = known ? Normal : Unlearned;
            var dim = known ? 1.0 : 0.6;
            if (!selected.Contains(sid))
            {
                total++;
                var shown = (!learnedOnly || known)
                    && (needle.Length == 0 || (name ?? "").Contains(
                            needle, StringComparison.OrdinalIgnoreCase));
                if (shown)
                    avail.Add(new AvailRow(name, Detail(s), colour, dim, known,
                        SpellIcon(sid), new Act(() => Fire($"{Root}/toggle/{sid}"))));
            }
            // Buffs : seuls les sorts appris peuvent etre lances sur soi, en
            // afficher 400 dont 380 impossibles ne servait a rien. Tant que le
            // serveur n'a pas confirme la liste, personne n'est « appris » : on
            // montre tout plutot qu'un panneau vide.
            if (known || !confirmed)
                buffs.Add(new BuffRow(name, onBuff.Contains(sid), colour, dim,
                    SpellIcon(sid), new Act(() => Fire($"{Root}/buff/{sid}"))));
        }
        // Les sorts appris d'abord : c'est ce qu'on cherche 9 fois sur 10.
        Available.ItemsSource = avail.OrderByDescending(r => r.Known).ToList();
        Buffs.ItemsSource = buffs;
        AvailCount.Text = avail.Count == total
            ? $"{total}" : $"{avail.Count} / {total}";
        EmptyAvail.IsVisible = total == 0 && picked.Count == 0;

        Move.IsChecked = _state.GetProperty("move").GetBoolean();
        _shownDelay = Math.Round(_state.GetProperty("delay").GetDouble() * 100);
        _shownSteps = _state.GetProperty("engage_max_steps").GetInt32();
        Delay.Value = _shownDelay;
        Steps.Value = _shownSteps;

        _painting = false;
    }

    void Fire(string url) => _ = Send(url);

    async void OnMove(object? sender, RoutedEventArgs e)
    {
        if (_painting) return;
        await Send($"{Root}/move/{(Move.IsChecked == true ? 1 : 0)}");
    }

    async void OnClear(object? sender, RoutedEventArgs e) => await Send($"{Root}/clear");

    void OnFilter(object? sender, RoutedEventArgs e) => Paint();
}

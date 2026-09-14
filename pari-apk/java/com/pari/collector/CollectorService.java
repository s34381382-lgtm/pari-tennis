package com.pari.collector;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.Environment;
import android.os.IBinder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

/**
 * Live-коллектор линии pari.ru по чистому API (без браузера).
 *   base:  GET /events/listBase?lang=ru&scopeMarket=2300  (один раз)
 *   delta: GET /events/list?lang=ru&version=V&scopeMarket=2300 (опрос)
 * Снимок: все live-матчи тенниса — счёт, очки, стата, ВСЕ кэфы с расшифровкой.
 * Формат line-YYYY-MM-DD.jsonl совместим с pari_view.py / pari_line.py.
 */
public class CollectorService extends Service {

    private static final String UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128";
    private static final String[] HOSTS = {
            "https://line-lb51-w.pb06e2-resources.com",
            "https://line-lb01-w.pb06e2-resources.com",
            "https://line-vk01-w.pb06e2-resources.ru"};
    private static final long POLL_MS = 10_000L;

    private volatile boolean stop = false;
    private File dir;
    private String day = "";
    private FileOutputStream out;
    private long rxBytes = 0;
    private long rxMark = 0;
    private int polls = 0;

    // состояние (только теннис — остальное выбрасываем сразу)
    private final Map<String, String> tours = new HashMap<>();
    private final Map<String, JSONObject> events = new HashMap<>();
    private final Map<String, JSONObject> live = new HashMap<>();
    private final Map<String, JSONObject> factors = new HashMap<>();
    private final Map<String, JSONObject> prematch = new HashMap<>();
    private final Map<String, JSONObject> miscs = new HashMap<>();
    private final Map<String, String> blocks = new HashMap<>();
    private final Map<String, String> prematchSent = new HashMap<>();
    private final Map<String, Map<String, Double>> oddsSent = new HashMap<>();
    private final Set<String> wasLive = new HashSet<>();
    private long catv = 0;
    private long catalogAt = 0;
    private int snapCount = 0;
    private final Map<String, String> fmap = new HashMap<>();
    private long version = 0;
    private final Set<String> seenNew = new HashSet<>();
    private static final Pattern TENNIS =
            Pattern.compile("WTA|ATP|ITF|UTR|еннис|Челленджер|челленджер");

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel("pari",
                "Pari Collector", NotificationManager.IMPORTANCE_LOW));
        Notification.Builder b = new Notification.Builder(this, "pari");
        b.setContentTitle("Pari Collector")
         .setContentText("Старт...")
         .setSmallIcon(android.R.drawable.stat_sys_download);
        PendingIntent pi = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        b.setContentIntent(pi);
        startForeground(1, b.build());
        new Thread(this::loop, "poller").start();
    }

    private File dataDir() {
        File ext = getExternalFilesDir(null);
        if (ext == null) ext = new File(Environment.getExternalStorageDirectory(),
                "Android/data/com.pari.collector/files");
        ext.mkdirs();
        return ext;
    }

    // ---------------- HTTP ----------------
    private int fastHost = 0; // липкий хост: работающий остаётся первым

    private String get(String path) throws Exception {
        Exception last = null;
        for (int k = 0; k < HOSTS.length; k++) {
            int hi = (fastHost + k) % HOSTS.length;
            String h = HOSTS[hi];
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(h + path).openConnection();
                c.setRequestProperty("User-Agent", UA);
                c.setRequestProperty("Origin", "https://pari.ru");
                c.setRequestProperty("Referer", "https://pari.ru/");
                c.setRequestProperty("Accept-Encoding", "gzip");
                c.setConnectTimeout(8_000);
                c.setReadTimeout(15_000);
                if (c.getResponseCode() != 200)
                    throw new java.io.IOException("HTTP " + c.getResponseCode());
                InputStream in = c.getInputStream();
                ByteArrayOutputStream buf = new ByteArrayOutputStream();
                byte[] b = new byte[32768];
                int n;
                while ((n = in.read(b)) > 0) buf.write(b, 0, n);
                in.close();
                byte[] data = buf.toByteArray();
                rxBytes += data.length;
                String enc = c.getHeaderField("Content-Encoding");
                if (data.length > 2 && (data[0] & 0xFF) == 0x1F && (data[1] & 0xFF) == 0x8B) {
                    GZIPInputStream g = new GZIPInputStream(new ByteArrayInputStream(data));
                    buf = new ByteArrayOutputStream();
                    while ((n = g.read(b)) > 0) buf.write(b, 0, n);
                    g.close();
                    data = buf.toByteArray();
                } else if ("gzip".equalsIgnoreCase(enc == null ? "" : enc)) {
                    GZIPInputStream g = new GZIPInputStream(new ByteArrayInputStream(data));
                    buf = new ByteArrayOutputStream();
                    while ((n = g.read(b)) > 0) buf.write(b, 0, n);
                    g.close();
                    data = buf.toByteArray();
                }
                fastHost = hi; // липкий хост: сработавший остаётся первым
                return new String(data, "UTF-8");
            } catch (Exception e) {
                last = e;
            } finally {
                if (c != null) c.disconnect();
            }
        }
        // все хосты отвалились — сбрасываем липкость, в следующий раз с первого
        fastHost = 0;
        throw last != null ? last : new java.io.IOException("all hosts down");
    }

    // ---------------- состояние ----------------

    private boolean isTennisEv(JSONObject e) {
        String t = tours.get(String.valueOf(e.optInt("sportId", -1)));
        return t != null && TENNIS.matcher(t).find();
    }

    private void applyTours(JSONArray arr) {
        if (arr == null) return;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject s = arr.optJSONObject(i);
            if (s != null) tours.put(String.valueOf(s.optInt("id")), s.optString("name", ""));
        }
    }

    private void applyEvents(JSONArray arr) {
        if (arr == null) return;
        long now = System.currentTimeMillis() / 1000;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject e = arr.optJSONObject(i);
            if (e == null) continue;
            String id = String.valueOf(e.optInt("id"));
            if ("live".equals(e.optString("place"))) {
                if (isTennisEv(e)) events.put(id, e);
                else events.remove(id);
                prematch.remove(id);
            } else if ("line".equals(e.optString("place"))) {
                events.remove(id);
                live.remove(id);
                factors.remove(id);
                // прематч тенниса со стартом <24ч — держим для базы закрывающих кэфов
                long st = e.optLong("startTime", 0);
                if (isTennisEv(e) && st > now && st - now < 86400) prematch.put(id, e);
                else prematch.remove(id);
            } else {
                events.remove(id);
                live.remove(id);
                factors.remove(id);
                prematch.remove(id);
            }
        }
    }

    private void applyLive(JSONArray arr) {
        if (arr == null) return;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject li = arr.optJSONObject(i);
            if (li == null) continue;
            String id = String.valueOf(li.optInt("eventId"));
            if (events.containsKey(id)) live.put(id, li);
        }
    }

    private void applyFactors(JSONArray arr) {
        if (arr == null) return;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject cf = arr.optJSONObject(i);
            if (cf == null) continue;
            String id = String.valueOf(cf.optInt("e"));
            if (!events.containsKey(id) && !prematch.containsKey(id)) continue;
            JSONObject old = factors.get(id);
            Map<String, JSONObject> merged = new HashMap<>();
            if (old != null) {
                JSONArray fa = old.optJSONArray("factors");
                if (fa != null) for (int k = 0; k < fa.length(); k++) {
                    JSONObject f = fa.optJSONObject(k);
                    if (f != null) merged.put(String.valueOf(f.optInt("f")), f);
                }
            }
            JSONArray fa = cf.optJSONArray("factors");
            if (fa != null) for (int k = 0; k < fa.length(); k++) {
                JSONObject f = fa.optJSONObject(k);
                if (f != null) merged.put(String.valueOf(f.optInt("f")), f);
            }
            JSONObject ne = new JSONObject();
            try {
                ne.put("countAll", cf.optInt("countAll"));
                JSONArray out = new JSONArray();
                for (JSONObject f : merged.values()) out.put(f);
                ne.put("factors", out);
            } catch (Exception ignored) {
            }
            factors.put(id, ne);
        }
    }

    private void applyBlocks(JSONArray arr) {
        if (arr == null) return;
        blocks.clear();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject b = arr.optJSONObject(i);
            if (b == null) continue;
            blocks.put(String.valueOf(b.optInt("eventId")), b.optString("state", ""));
        }
    }

    private void applyMiscs(JSONArray arr) {        if (arr == null) return;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject m = arr.optJSONObject(i);
            if (m == null) continue;
            String id = String.valueOf(m.optInt("id"));
            if (events.containsKey(id)) miscs.put(id, m);
        }
    }

    private void loadCatalog() {
        try {
            JSONObject j = new JSONObject(
                    get("/line/factorsCatalog/tables?version=0&lang=ru&sysId=22"));
            catv = j.optLong("version", 0);
            JSONArray groups = j.optJSONArray("groups");
            if (groups == null) return;
            for (int gi = 0; gi < groups.length(); gi++) {
                JSONObject g = groups.optJSONObject(gi);
                if (g == null) continue;
                JSONArray tables = g.optJSONArray("tables");
                if (tables == null) continue;
                for (int ti = 0; ti < tables.length(); ti++) {
                    JSONObject t = tables.optJSONObject(ti);
                    if (t == null) continue;
                    JSONArray rows = t.optJSONArray("rows");
                    if (rows == null || rows.length() == 0) continue;
                    JSONArray head = rows.optJSONArray(0);
                    for (int ri = 1; ri < rows.length(); ri++) {
                        JSONArray r = rows.optJSONArray(ri);
                        if (r == null) continue;
                        String rn = r.optJSONObject(0) != null
                                ? r.optJSONObject(0).optString("name", "") : "";
                        for (int ci = 0; ci < r.length(); ci++) {
                            JSONObject c = r.optJSONObject(ci);
                            if (c != null && c.has("factorId")) {
                                String col = (head != null && ci < head.length()
                                        && head.optJSONObject(ci) != null)
                                        ? head.optJSONObject(ci).optString("name", "") : "";
                                String nm = g.optString("name", "");
                                if (!col.isEmpty()) nm += " / " + col;
                                if (!rn.isEmpty()) nm += " / " + rn;
                                fmap.put(String.valueOf(c.optInt("factorId")), nm);
                            }
                        }
                    }
                }
            }
            log("catalog: " + fmap.size() + " factors");
        } catch (Exception e) {
            log("catalog fail: " + e);
        }
    }

    // ---------------- снимок ----------------

    private JSONObject buildSnapshot() {
        JSONObject snap = new JSONObject();
        JSONArray arr = new JSONArray();
        JSONArray fresh = new JSONArray();
        try {
            snap.put("ts", System.currentTimeMillis() / 1000);
            snap.put("catv", catv);
            snapCount++;
            for (Map.Entry<String, JSONObject> en : events.entrySet()) {
                String eid = en.getKey();
                JSONObject e = en.getValue();
                JSONObject m = new JSONObject();
                m.put("eid", Integer.parseInt(eid));
                m.put("tour", tours.get(String.valueOf(e.optInt("sportId", -1))));
                m.put("p1", e.optString("team1", null));
                m.put("p2", e.optString("team2", null));
                m.put("t1id", e.optInt("team1Id"));
                m.put("t2id", e.optInt("team2Id"));
                m.put("start", e.optInt("startTime"));
                JSONObject mc = miscs.get(eid);
                if (mc != null) {
                    // таймер матча + задержка трансляции (для пауз/длительности)
                    if (mc.has("timerSeconds")) m.put("mtimer", mc.optInt("timerSeconds"));
                    if (mc.has("liveDelay")) m.put("mdelay", mc.optInt("liveDelay"));
                }
                String bs = blocks.get(eid);
                if (bs != null && !bs.isEmpty() && !"active".equals(bs)) m.put("blocked", bs);
                JSONObject li = live.get(eid);
                if (li != null) {
                    String cm = li.optString("scoreComment", "");
                    if (!cm.isEmpty()) m.put("cm", cm);
                    JSONArray sc = li.optJSONArray("scores");
                    if (sc != null && sc.length() > 0) {
                        JSONArray s0 = sc.optJSONArray(0);
                        if (s0 != null && s0.length() > 0)
                            m.put("sets", new JSONArray()
                                    .put(s0.optJSONObject(0).optString("c1"))
                                    .put(s0.optJSONObject(0).optString("c2")));
                    }
                    if (sc != null && sc.length() > 1) {
                        JSONArray ss = new JSONArray();
                        JSONArray s1 = sc.optJSONArray(1);
                        if (s1 != null) for (int k = 0; k < s1.length(); k++) {
                            JSONObject x = s1.optJSONObject(k);
                            if (x != null) ss.put(new JSONArray()
                                    .put(x.optString("c1")).put(x.optString("c2")));
                        }
                        m.put("set_scores", ss);
                    }
                    if (sc != null && sc.length() > 2) {
                        JSONArray s2 = sc.optJSONArray(2);
                        if (s2 != null && s2.length() > 0) {
                            JSONObject gg = s2.optJSONObject(0);
                            m.put("game", new JSONArray()
                                    .put(gg.optString("c1")).put(gg.optString("c2")));
                            if (gg.has("serve")) m.put("serve", gg.optInt("serve"));
                        }
                    }
                    JSONArray subs = li.optJSONArray("subscores");
                    if (subs != null) {
                        JSONObject stats = new JSONObject();
                        for (int k = 0; k < subs.length(); k++) {
                            JSONObject s = subs.optJSONObject(k);
                            if (s == null) continue;
                            String n = s.optString("kindName", "");
                            if (!n.isEmpty() && !n.contains("сет")
                                    && !n.toLowerCase(Locale.US).contains("тайм")) {
                                JSONObject st = new JSONObject();
                                st.put("c1", s.optString("c1"));
                                st.put("c2", s.optString("c2"));
                                st.put("by_set", s.optString("comment", ""));
                                stats.put(n, st);
                            }
                        }
                        if (stats.length() > 0) m.put("stats", stats);
                    }
                }
                JSONObject cf = factors.get(eid);
                if (cf != null) {
                    JSONArray fa = cf.optJSONArray("factors");
                    if (fa != null) {
                        // ДИФЫ: шлём только изменившиеся кэфы + полный кадр раз в час
                        boolean fullFrame = (snapCount % 360 == 0)
                                || !oddsSent.containsKey(eid);
                        Map<String, Double> sent = oddsSent.get(eid);
                        if (sent == null) {
                            sent = new HashMap<>();
                            oddsSent.put(eid, sent);
                        }
                        JSONArray odds = new JSONArray();
                        for (int k = 0; k < fa.length(); k++) {
                            JSONObject f = fa.optJSONObject(k);
                            if (f == null) continue;
                            String fk = f.optInt("f") + "|"
                                    + (f.has("pt") ? f.optString("pt") : "");
                            double v = f.optDouble("v");
                            Double was = sent.get(fk);
                            if (!fullFrame && was != null
                                    && Math.abs(was - v) < 0.0005) continue;
                            sent.put(fk, v);
                            JSONObject o = new JSONObject();
                            String nm = fmap.get(String.valueOf(f.optInt("f")));
                            o.put("m", nm != null ? nm : ("f" + f.optInt("f")));
                            o.put("f", f.optInt("f"));
                            o.put("v", v);
                            if (f.has("pt")) o.put("pt", f.optString("pt"));
                            odds.put(o);
                        }
                        if (fullFrame || odds.length() > 0) m.put("odds", odds);
                        m.put("n_odds", cf.optInt("countAll"));
                    }
                }
                arr.put(m);
                wasLive.add(eid);
                // свежий: ПЕРВЫЙ СЧЁТ в матче (имена + set_scores), 0:0, геймов <= 2
                try {
                    if (e.optString("team1", "").length() < 2) continue;
                    JSONArray ss = m.optJSONArray("set_scores");
                    if (ss == null || ss.length() == 0) continue;
                    JSONArray sets = m.optJSONArray("sets");
                    boolean zero = sets != null && "0".equals(sets.optString(0))
                            && "0".equals(sets.optString(1));
                    int games = 99;
                    if (ss != null && ss.length() > 0) {
                        JSONArray last = ss.optJSONArray(ss.length() - 1);
                        games = Integer.parseInt(last.optString(0))
                                + Integer.parseInt(last.optString(1));
                    } else if (zero) games = 0;
                    if (zero && games <= 2 && seenNew.add(eid)) {
                        JSONObject f = new JSONObject();
                        f.put("eid", Integer.parseInt(eid));
                        f.put("p1", e.optString("team1"));
                        f.put("p2", e.optString("team2"));
                        fresh.put(f);
                    }
                } catch (Exception ignored) {
                }
            }
            snap.put("n", arr.length());
            snap.put("matches", arr);
            if (fresh.length() > 0) snap.put("fresh", fresh);
            // финалы: были в live, пропали (с последним известным счётом)
            JSONArray finals = new JSONArray();
            for (String id : new HashSet<>(wasLive)) {
                if (!events.containsKey(id)) {
                    JSONObject f = new JSONObject();
                    try {
                        f.put("eid", Integer.parseInt(id));
                        JSONObject li = live.get(id);
                        if (li != null) {
                            f.put("comment", li.optString("scoreComment", ""));
                            JSONArray sc = li.optJSONArray("scores");
                            if (sc != null && sc.length() > 1) {
                                JSONArray s1 = sc.optJSONArray(1);
                                JSONArray fin = new JSONArray();
                                if (s1 != null) for (int k = 0; k < s1.length(); k++) {
                                    JSONObject x = s1.optJSONObject(k);
                                    if (x != null) fin.put(x.optString("c1") + "-"
                                            + x.optString("c2"));
                                }
                                f.put("final", fin);
                            }
                        }
                        finals.put(f);
                    } catch (Exception ignored) {
                    }
                    wasLive.remove(id);
                    live.remove(id);
                    factors.remove(id);
                    miscs.remove(id);
                    oddsSent.remove(id);
                }
            }
            if (finals.length() > 0) snap.put("finals", finals);
            // прематч: только изменившиеся кэфы + часовой полный кадр.
            // Сигнатура = win-пара + число рынков (ловит главные движения дёшево)
            if (!prematch.isEmpty() && (snapCount % 10 == 0 || snapCount % 360 == 0)) {
                JSONArray pm = new JSONArray();
                boolean keyframe = (snapCount % 360 == 0);
                for (Map.Entry<String, JSONObject> en : prematch.entrySet()) {
                    JSONObject e = en.getValue();
                    JSONObject cf = factors.get(en.getKey());
                    String sig = prematchSig(cf);
                    String old = prematchSent.get(en.getKey());
                    if (!keyframe && sig.equals(old)) continue;
                    prematchSent.put(en.getKey(), sig);
                    JSONObject o = new JSONObject();
                    o.put("eid", e.optInt("id"));
                    o.put("tour", tours.get(String.valueOf(e.optInt("sportId", -1))));
                    o.put("p1", e.optString("team1", null));
                    o.put("p2", e.optString("team2", null));
                    o.put("start", e.optLong("startTime"));
                    if (cf != null) {
                        JSONArray fa = cf.optJSONArray("factors");
                        if (fa != null) {
                            JSONArray odds = new JSONArray();
                            for (int k = 0; k < fa.length(); k++) {
                                JSONObject f2 = fa.optJSONObject(k);
                                if (f2 == null) continue;
                                JSONObject oo = new JSONObject();
                                String nm = fmap.get(String.valueOf(f2.optInt("f")));
                                oo.put("m", nm != null ? nm : ("f" + f2.optInt("f")));
                                oo.put("f", f2.optInt("f"));
                                oo.put("v", f2.optDouble("v"));
                                if (f2.has("pt")) oo.put("pt", f2.optString("pt"));
                                odds.put(oo);
                            }
                            o.put("odds", odds);
                        }
                    }
                    pm.put(o);
                }
                if (pm.length() > 0) snap.put("prematch", pm);
            }
        } catch (Exception e) {
            log("snapshot fail: " + e);
            return null;
        }
        return snap;
    }

    private String prematchSig(JSONObject cf) {
        if (cf == null) return "-";
        JSONArray fa = cf.optJSONArray("factors");
        if (fa == null) return String.valueOf(cf.optInt("countAll"));
        String w1 = "", w2 = "";
        for (int k = 0; k < fa.length(); k++) {
            JSONObject f = fa.optJSONObject(k);
            if (f == null) continue;
            if (f.optInt("f") == 921) w1 = String.valueOf(f.optDouble("v"));
            if (f.optInt("f") == 923) w2 = String.valueOf(f.optDouble("v"));
        }
        return cf.optInt("countAll") + "|" + w1 + "|" + w2;
    }

    // ---------------- файлы ----------------

    private synchronized void writeLine(JSONObject snap) throws Exception {
        String stamp = new SimpleDateFormat("yyyy-MM-dd-HH", Locale.US).format(new Date());
        if (!stamp.equals(day)) {
            if (out != null) out.close();
            gzipOlder(stamp.substring(0, 10));
            day = stamp;
            out = new FileOutputStream(new File(dir, "line-" + stamp + ".jsonl"), true);
        }
        out.write((snap.toString() + "\n").getBytes("UTF-8"));
        out.flush();
        polls++;
        long now = System.currentTimeMillis();
        if (now - rxMark > 3600_000) {
            log(String.format(Locale.US, "traffic hour: %.1f MB in %d polls (avg %.0f KB)",
                    rxBytes / 1048576.0, polls, polls > 0 ? rxBytes / 1024.0 / polls : 0));
            rxBytes = 0;
            polls = 0;
            rxMark = now;
        }
    }

    private void gzipOlder(String today) {
        try {
            File[] fs = dir.listFiles((d, n) -> n.startsWith("line-") && n.endsWith(".jsonl"));
            if (fs == null) return;
            for (File f : fs) {
                String d = f.getName().substring(5, 15);
                if (d.compareTo(today) >= 0) continue;
                File gz = new File(f.getParentFile(), f.getName() + ".gz");
                if (gz.exists()) continue;
                GZIPOutputStream g = new GZIPOutputStream(new FileOutputStream(gz));
                InputStream in = new java.io.FileInputStream(f);
                byte[] b = new byte[32768];
                int n;
                while ((n = in.read(b)) > 0) g.write(b, 0, n);
                in.close();
                g.close();
                f.delete();
            }
        } catch (Exception e) {
            log("gzip fail: " + e);
        }
    }

    private void updateNotif(int n, int fresh) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        Notification.Builder b = new Notification.Builder(this, "pari");
        b.setContentTitle("Pari Collector")
         .setContentText("Live-теннис: " + n + (fresh > 0 ? " | новых: " + fresh : ""))
         .setSmallIcon(android.R.drawable.stat_sys_download);
        nm.notify(1, b.build());
    }

    private void log(String s) {
        try {
            FileOutputStream f = new FileOutputStream(new File(dir, "collector.log"), true);
            f.write((new Date() + " " + s + "\n").getBytes("UTF-8"));
            f.close();
        } catch (Exception ignored) {
        }
    }

    // ---------------- цикл ----------------

    private void loop() {
        dir = dataDir();
        int fails = 0;
        loadCatalog();
        try {
            JSONObject base = new JSONObject(
                    get("/events/listBase?lang=ru&scopeMarket=2300"));
            applyTours(base.optJSONArray("sports"));
            applyEvents(base.optJSONArray("events"));
            applyLive(base.optJSONArray("liveEventInfos"));
            applyFactors(base.optJSONArray("customFactors"));
            applyMiscs(base.optJSONArray("eventMiscs"));
            applyBlocks(base.optJSONArray("eventBlocks"));
            version = base.optLong("packetVersion", 0);
            log("boot ok, version=" + version + " tours=" + tours.size());
        } catch (Exception e) {
            log("boot fail: " + e);
        }
        while (!stop) {
            try {
                if (System.currentTimeMillis() - catalogAt > 6 * 3600_000L) {
                    long before = catv;
                    loadCatalog();
                    catalogAt = System.currentTimeMillis();
                    if (catv != 0 && catv != before)
                        log("catalog changed: " + before + " -> " + catv);
                }
                JSONObject d = new JSONObject(get("/events/list?lang=ru&version="
                        + version + "&scopeMarket=2300"));
                applyTours(d.optJSONArray("sports"));
                applyEvents(d.optJSONArray("events"));
                applyLive(d.optJSONArray("liveEventInfos"));
                applyFactors(d.optJSONArray("customFactors"));
                applyMiscs(d.optJSONArray("eventMiscs"));
                applyBlocks(d.optJSONArray("eventBlocks"));
                long v = d.optLong("packetVersion", 0);
                if (v > 0) version = v;
                JSONObject snap = buildSnapshot();
                if (snap != null) {
                    writeLine(snap);
                    updateNotif(snap.optInt("n", 0),
                            snap.optJSONArray("fresh") != null
                                    ? snap.optJSONArray("fresh").length() : 0);
                }
                fails = 0;
            } catch (Exception e) {
                fails++;
                log("poll fail x" + fails + ": " + e);
                if (fails % 20 == 0) {
                    // раз в ~3 мин пробуем перечитать базу (версия могла уплыть)
                    try {
                        JSONObject base = new JSONObject(
                                get("/events/listBase?lang=ru&scopeMarket=2300"));
                        applyTours(base.optJSONArray("sports"));
                        applyEvents(base.optJSONArray("events"));
                        applyLive(base.optJSONArray("liveEventInfos"));
                        applyFactors(base.optJSONArray("customFactors"));
            applyMiscs(base.optJSONArray("eventMiscs"));
            applyBlocks(base.optJSONArray("eventBlocks"));
                        version = base.optLong("packetVersion", version);
                        log("reboot ok");
                    } catch (Exception ignored) {
                    }
                }
            }
            try {
                Thread.sleep(POLL_MS * (fails >= 5 ? 6 : 1));
            } catch (InterruptedException ie) {
                break;
            }
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int id) {
        if (intent != null && "STOP".equals(intent.getAction())) {
            stop = true;
            stopSelf();
            return START_NOT_STICKY;
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent i) {
        return null;
    }

    @Override
    public void onDestroy() {
        stop = true;
        try {
            if (out != null) out.close();
        } catch (Exception ignored) {
        }
        super.onDestroy();
    }
}

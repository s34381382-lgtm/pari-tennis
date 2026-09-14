package com.pari.collector;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;

public class MainActivity extends Activity {

    private final Handler h = new Handler();
    private TextView tv;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        tv = new TextView(this);
        tv.setPadding(32, 48, 32, 48);
        tv.setTextSize(14);
        setContentView(tv);
        startForegroundService(new Intent(this, CollectorService.class));
        askBatteryOk();
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        h.postDelayed(this::refresh, 2000);
    }

    private void askBatteryOk() {
        try {
            android.os.PowerManager pm =
                    (android.os.PowerManager) getSystemService(POWER_SERVICE);
            if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())) {
                Intent i = new Intent(
                        android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        android.net.Uri.parse("package:" + getPackageName()));
                startActivity(i);
            }
        } catch (Exception ignored) {
        }
    }

    private void refresh() {
        StringBuilder sb = new StringBuilder("PARI Collector\n\n");
        try {
            File dir = getExternalFilesDir(null);
            File[] fs = dir == null ? new File[0]
                    : dir.listFiles((d, n) -> n.startsWith("line-"));
            long total = 0;
            if (fs != null) {
                java.util.Arrays.sort(fs, (a, c) -> c.getName().compareTo(a.getName()));
                for (File f : fs) {
                    sb.append(f.getName()).append("  ")
                      .append(f.length() / 1024).append(" KB\n");
                    total += f.length();
                }
            }
            sb.append("\nВсего: ").append(total / 1024).append(" KB\n\n");
            File logf = new File(dir, "collector.log");
            if (logf.exists()) {
                sb.append("--- последние события ---\n");
                BufferedReader r = new BufferedReader(new FileReader(logf));
                java.util.ArrayList<String> lines = new java.util.ArrayList<>();
                String l;
                while ((l = r.readLine()) != null) lines.add(l);
                r.close();
                for (int i = Math.max(0, lines.size() - 12); i < lines.size(); i++)
                    sb.append(lines.get(i)).append('\n');
            }
        } catch (Exception e) {
            sb.append("err: ").append(e);
        }
        tv.setText(sb.toString());
    }
}

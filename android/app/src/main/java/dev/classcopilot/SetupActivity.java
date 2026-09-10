package dev.classcopilot;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * First-run screen. Takes the laptop's address plus the 6-digit PIN, exchanges
 * them for a token via /api/pair, and stores both so the phone never has to
 * pair again.
 */
public class SetupActivity extends Activity {

    static final String PREFS = "class-copilot";
    static final String KEY_BASE = "base";
    static final String KEY_TOKEN = "token";

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());

    private EditText hostField;
    private EditText pinField;
    private Button connectButton;
    private TextView status;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        boolean reconfigure = getIntent().getBooleanExtra("reconfigure", false);
        // A scanned classcopilot:// link always means "pair with this laptop",
        // even if we are already paired with a different one.
        boolean fromLink = getIntent().getData() != null;
        if (!reconfigure && !fromLink
                && prefs.getString(KEY_BASE, null) != null
                && prefs.getString(KEY_TOKEN, null) != null) {
            openMain();
            return;
        }

        setContentView(R.layout.activity_setup);
        hostField = findViewById(R.id.host);
        pinField = findViewById(R.id.pin);
        connectButton = findViewById(R.id.connect);
        status = findViewById(R.id.status);

        String savedBase = prefs.getString(KEY_BASE, null);
        if (savedBase != null) {
            hostField.setText(savedBase.replaceFirst("^https?://", ""));
        }

        // classcopilot://10.0.0.5:8756/?pin=123456 -- prefills both fields.
        Uri data = getIntent().getData();
        if (data != null) {
            String authority = data.getAuthority();
            if (!TextUtils.isEmpty(authority)) {
                hostField.setText(authority);
            }
            String pin = data.getQueryParameter("pin");
            if (!TextUtils.isEmpty(pin)) {
                pinField.setText(pin);
            }
        }

        connectButton.setOnClickListener(v -> pair());
    }

    private String normalise(String raw) {
        String host = raw.trim();
        if (host.isEmpty()) {
            return null;
        }
        if (!host.startsWith("http://") && !host.startsWith("https://")) {
            host = "http://" + host;
        }
        while (host.endsWith("/")) {
            host = host.substring(0, host.length() - 1);
        }
        // Only a bare LAN address needs the default port. A hosted https URL
        // like https://something.hf.space already resolves on 443.
        String afterScheme = host.substring(host.indexOf("://") + 3);
        boolean hasPort = afterScheme.contains(":");
        boolean hasPath = afterScheme.contains("/");
        if (!hasPort && !hasPath && host.startsWith("http://")) {
            host = host + ":8756";
        }
        return host;
    }

    private void pair() {
        final String base = normalise(hostField.getText().toString());
        final String pin = pinField.getText().toString().trim();
        if (base == null) {
            status.setText("Enter the address printed on your laptop.");
            return;
        }
        if (pin.length() != 6) {
            status.setText("The PIN is 6 digits.");
            return;
        }

        connectButton.setEnabled(false);
        connectButton.setText(R.string.connecting);
        status.setText("");

        io.execute(() -> {
            String token = null;
            String error = null;
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(base + "/api/pair").openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("content-type", "application/json");
                conn.setConnectTimeout(6000);
                conn.setReadTimeout(6000);
                conn.setDoOutput(true);

                JSONObject body = new JSONObject();
                body.put("pin", pin);
                body.put("name", android.os.Build.MODEL);
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes("UTF-8"));
                }

                int code = conn.getResponseCode();
                if (code == 200) {
                    token = new JSONObject(read(conn.getInputStream())).getString("token");
                } else if (code == 403) {
                    error = "That PIN was not accepted. Check the laptop terminal —\n"
                            + "the PIN changes every time you restart it.";
                } else {
                    error = "The laptop answered with error " + code + ".";
                }
            } catch (Exception e) {
                error = "Could not reach " + base + ".\n\n"
                        + "Check that both devices are on the same wifi and that\n"
                        + "class-copilot is running on the laptop.";
            } finally {
                if (conn != null) {
                    conn.disconnect();
                }
            }

            final String gotToken = token;
            final String gotError = error;
            ui.post(() -> {
                connectButton.setEnabled(true);
                connectButton.setText(R.string.connect);
                if (gotToken != null) {
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                            .putString(KEY_BASE, base)
                            .putString(KEY_TOKEN, gotToken)
                            .apply();
                    openMain();
                } else {
                    status.setText(gotError);
                }
            });
        });
    }

    private static String read(java.io.InputStream in) throws Exception {
        java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
        byte[] chunk = new byte[4096];
        int n;
        while ((n = in.read(chunk)) > 0) {
            buffer.write(chunk, 0, n);
        }
        return buffer.toString("UTF-8");
    }

    private void openMain() {
        startActivity(new Intent(this, MainActivity.class));
        finish();
    }

    @Override
    protected void onDestroy() {
        io.shutdownNow();
        super.onDestroy();
    }
}

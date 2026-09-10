package dev.classcopilot;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.pm.PackageManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.JavascriptInterface;
import android.webkit.WebViewClient;

/**
 * The whole app: a WebView on the laptop's chat page, with the screen kept
 * awake so it does not blank while you are teaching.
 */
public class MainActivity extends Activity {

    private static final int FILE_REQUEST = 11;

    private WebView web;
    private String base;
    private boolean failed = false;
    private ValueCallback<Uri[]> pendingFiles;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);

        SharedPreferences prefs = getSharedPreferences(SetupActivity.PREFS, MODE_PRIVATE);
        base = prefs.getString(SetupActivity.KEY_BASE, null);
        String token = prefs.getString(SetupActivity.KEY_TOKEN, null);
        if (base == null || token == null) {
            reconfigure();
            return;
        }

        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            getWindow().setStatusBarColor(Color.parseColor("#151A21"));
            getWindow().setNavigationBarColor(Color.parseColor("#151A21"));
        }

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0E1116"));
        setContentView(web);

        // Without this the page is laid out behind the status bar and the top
        // row of buttons is unreachable.
        web.setOnApplyWindowInsetsListener((v, insets) -> {
            int top, bottom, left, right;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                android.graphics.Insets bars =
                        insets.getInsets(WindowInsets.Type.systemBars()
                                | WindowInsets.Type.displayCutout());
                top = bars.top; bottom = bars.bottom; left = bars.left; right = bars.right;
            } else {
                top = insets.getSystemWindowInsetTop();
                bottom = insets.getSystemWindowInsetBottom();
                left = insets.getSystemWindowInsetLeft();
                right = insets.getSystemWindowInsetRight();
            }
            v.setPadding(left, top, right, bottom);
            return insets;
        });
        web.requestApplyInsets();

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // the token lives in localStorage
        // The page is responsive; honour its <meta viewport> instead of laying
        // it out at the default desktop width, which forced sideways scrolling.
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setTextZoom(100);                    // ignore the system font scale
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // Everything we serve is same-origin; keep it inside the app.
                return false;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request.isForMainFrame()) {
                    failed = true;
                    showUnreachable();
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view,
                                             ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (pendingFiles != null) {
                    pendingFiles.onReceiveValue(null);
                }
                pendingFiles = callback;
                try {
                    Intent pick = params.createIntent();
                    pick.addCategory(Intent.CATEGORY_OPENABLE);
                    startActivityForResult(Intent.createChooser(pick, "Choose a file"),
                                           FILE_REQUEST);
                    return true;
                } catch (Exception e) {
                    pendingFiles = null;
                    return false;
                }
            }
        });
        web.addJavascriptInterface(new Bridge(), "ccNative");
        web.loadUrl(base + "/?app=1&token=" + android.net.Uri.encode(token));
        // Start muted. You unmute when a doubt comes, which keeps the battery,
        // the transcript and the bill free of everything else.
        askPermissionOnce();
    }

    /** The page calls these to drive the microphone. */
    private class Bridge {
        @JavascriptInterface
        public boolean micOn() {
            return MicService.running;
        }

        @JavascriptInterface
        public boolean hasPermission() {
            return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                    || checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                       == PackageManager.PERMISSION_GRANTED;
        }

        @JavascriptInterface
        public void setMic(boolean on) {
            runOnUiThread(() -> {
                if (!on) {
                    stopService(new Intent(MainActivity.this, MicService.class));
                } else if (hasPermission()) {
                    startMic();
                } else {
                    askPermissionOnce();
                }
            });
        }
    }

    private boolean hasPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                || checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                   == PackageManager.PERMISSION_GRANTED;
    }

    private void askPermissionOnce() {
        if (hasPermission()) {
            return;
        }
        String[] wanted = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                ? new String[]{Manifest.permission.RECORD_AUDIO,
                               Manifest.permission.POST_NOTIFICATIONS}
                : new String[]{Manifest.permission.RECORD_AUDIO};
        requestPermissions(wanted, 7);
    }

    private void startMic() {
        Intent svc = new Intent(this, MicService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code != 7) {
            return;
        }
        boolean granted = false;
        for (int i = 0; i < perms.length; i++) {
            if (Manifest.permission.RECORD_AUDIO.equals(perms[i])
                    && results[i] == PackageManager.PERMISSION_GRANTED) {
                granted = true;
            }
        }
        if (granted) {
            // Permission just arrived; stay muted until you ask for it.
            web.evaluateJavascript("window.ccMicReady && window.ccMicReady()", null);
        } else {
            new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                    .setTitle("Microphone needed")
                    .setMessage("Without the microphone this phone can only show "
                            + "answers, not hear the class. You can still use it if "
                            + "the laptop does its own listening.")
                    .setPositiveButton("OK", null)
                    .show();
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request != FILE_REQUEST) {
            super.onActivityResult(request, result, data);
            return;
        }
        if (pendingFiles == null) {
            return;
        }
        Uri[] picked = null;
        if (result == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                int n = data.getClipData().getItemCount();
                picked = new Uri[n];
                for (int i = 0; i < n; i++) {
                    picked[i] = data.getClipData().getItemAt(i).getUri();
                }
            } else if (data.getData() != null) {
                picked = new Uri[]{data.getData()};
            }
        }
        pendingFiles.onReceiveValue(picked);   // null tells the page it was cancelled
        pendingFiles = null;
    }

    private void showUnreachable() {
        new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                .setTitle("Laptop not reachable")
                .setMessage("Could not reach " + base + ".\n\n"
                        + "Make sure class-copilot is running and that this phone is on "
                        + "the same wifi. If the laptop's address changed, set it again.")
                .setPositiveButton("Retry", (d, w) -> {
                    failed = false;
                    web.reload();
                })
                .setNegativeButton("Change server", (d, w) -> reconfigure())
                .setCancelable(false)
                .show();
    }

    private void reconfigure() {
        Intent i = new Intent(this, SetupActivity.class);
        i.putExtra("reconfigure", true);
        startActivity(i);
        finish();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                .setTitle("class-copilot")
                .setMessage("Leave the answer screen?")
                .setPositiveButton("Leave", (d, w) -> super.onBackPressed())
                .setNeutralButton("Change server", (d, w) -> reconfigure())
                .setNegativeButton("Stay", null)
                .show();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null && failed) {
            failed = false;
            web.reload();
        }
        hideSystemBars();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemBars();
        }
    }

    /**
     * Deliberately does not go immersive. Hiding the bars made the page lay out
     * behind the status bar, which put the top row of controls out of reach.
     */
    private void hideSystemBars() {
        if (web != null) {
            web.requestApplyInsets();
        }
    }

    @Override
    protected void onDestroy() {
        // Stop listening when the app really goes away, not on rotation.
        if (isFinishing()) {
            stopService(new Intent(this, MicService.class));
        }
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}

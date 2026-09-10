package dev.classcopilot;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Turns the phone into the microphone for a laptop that cannot hear the class.
 *
 * Records 16 kHz mono PCM16 and pushes it to the server as one long chunked
 * POST. A foreground service, because recording has to survive the screen
 * turning off while you teach.
 */
public class MicService extends Service {

    private static final String TAG = "MicService";
    private static final String CHANNEL = "mic";
    private static final int NOTE_ID = 42;
    private static final int RATE = 16000;

    public static final String ACTION_STOP = "dev.classcopilot.STOP_MIC";
    public static volatile boolean running = false;

    private volatile boolean stopping = false;
    private Thread worker;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (running) {
            return START_STICKY;
        }

        startForeground(NOTE_ID, buildNotification());
        running = true;
        stopping = false;
        worker = new Thread(this::pump, "mic-pump");
        worker.start();
        return START_STICKY;
    }

    private Notification buildNotification() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL, "Listening", NotificationManager.IMPORTANCE_LOW);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }

        Intent stop = new Intent(this, MicService.class).setAction(ACTION_STOP);
        int flag = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                ? PendingIntent.FLAG_IMMUTABLE : 0;
        PendingIntent stopPi = PendingIntent.getService(this, 1, stop, flag);

        PendingIntent openPi = PendingIntent.getActivity(
                this, 0, new Intent(this, MainActivity.class), flag);

        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);

        return b.setContentTitle("class-copilot is listening")
                .setContentText("Sending what this phone hears to your laptop")
                .setSmallIcon(android.R.drawable.presence_audio_online)
                .setOngoing(true)
                .setContentIntent(openPi)
                .addAction(new Notification.Action.Builder(null, "Stop", stopPi).build())
                .build();
    }

    /** Record and upload, reconnecting for as long as the service lives. */
    private void pump() {
        SharedPreferences prefs = getSharedPreferences(SetupActivity.PREFS, MODE_PRIVATE);
        String base = prefs.getString(SetupActivity.KEY_BASE, null);
        String token = prefs.getString(SetupActivity.KEY_TOKEN, "");
        if (base == null) {
            stopSelf();
            return;
        }

        int minBuf = AudioRecord.getMinBufferSize(
                RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minBuf <= 0) {
            minBuf = RATE * 2;
        }
        int bufBytes = Math.max(minBuf, RATE);      // ~0.5 s of headroom

        while (!stopping) {
            AudioRecord rec = null;
            HttpURLConnection conn = null;
            try {
                rec = new AudioRecord(
                        MediaRecorder.AudioSource.VOICE_RECOGNITION,
                        RATE, AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT, bufBytes);
                if (rec.getState() != AudioRecord.STATE_INITIALIZED) {
                    throw new IllegalStateException("microphone unavailable");
                }

                conn = (HttpURLConnection) new URL(base + "/api/audio").openConnection();
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setUseCaches(false);
                conn.setConnectTimeout(6000);
                conn.setChunkedStreamingMode(4096);   // stream, never buffer it all
                conn.setRequestProperty("content-type", "application/octet-stream");
                conn.setRequestProperty("x-cc-token", token);
                conn.setRequestProperty("x-cc-device", Build.MODEL);
                conn.setRequestProperty("x-cc-intent", "ask");

                OutputStream out = conn.getOutputStream();
                rec.startRecording();

                byte[] buf = new byte[3200];          // 100 ms per write
                while (!stopping) {
                    int n = rec.read(buf, 0, buf.length);
                    if (n > 0) {
                        out.write(buf, 0, n);
                        out.flush();
                    } else if (n < 0) {
                        throw new IllegalStateException("microphone read failed: " + n);
                    }
                }
                out.close();
                conn.getResponseCode();
            } catch (Exception e) {
                Log.w(TAG, "stream dropped: " + e);
            } finally {
                if (rec != null) {
                    try {
                        rec.stop();
                    } catch (Exception ignored) {
                    }
                    rec.release();
                }
                if (conn != null) {
                    conn.disconnect();
                }
            }

            if (!stopping) {
                try {
                    Thread.sleep(1500);               // wifi hiccup; try again
                } catch (InterruptedException e) {
                    break;
                }
            }
        }
    }

    @Override
    public void onDestroy() {
        stopping = true;
        running = false;
        if (worker != null) {
            worker.interrupt();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}

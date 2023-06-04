package com.example.simpleapplication;

import android.app.Activity;
import android.widget.TextView;

public class MainActivity extends Activity {

    private final Greeting greeting = new Greeting();

    @Override
    protected void onCreate(android.os.Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        TextView text = findViewById(R.id.text);
        text.setText(greeting.greet());
    }
}

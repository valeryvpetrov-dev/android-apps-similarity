package com.example.simpleapplication;

import com.example.simpleapplication.something.Used;

public class Greeting {

    private Used used;

    public Greeting() {
        used = new Used();
    }

    public String greet() {
        long side = used.tossACoin();
        String message;
        if (side == 0) {
            message = "obverse";
        } else if (side == 1) {
            message = "reverse";
        } else {
            message = "is it coin?";
        }
        return String.format("Hello! Toss a coin: %s", message);
    }
}

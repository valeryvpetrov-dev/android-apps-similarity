package com.example.simpleapplication.something;

public class Used {

    private static final int MAGIC = 3;

    public long tossACoin() {
        return Math.round(Math.random() * MAGIC);
    }
}

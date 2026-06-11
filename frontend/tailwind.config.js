export default {
    content: ["./index.html", "./src/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                hydra: {
                    bg: "#F7F9FC",
                    primary: "#2563EB",
                    rainfall: "#3B82F6",
                    soil: "#22C55E",
                    runoff: "#8B5CF6",
                    flood: "#EF4444",
                    drought: "#F59E0B",
                },
            },
            boxShadow: {
                glass: "0 12px 36px rgba(15, 23, 42, 0.10)",
            },
            borderRadius: {
                shell: "20px",
            },
            transitionDuration: {
                300: "300ms",
            },
        },
    },
    plugins: [],
};

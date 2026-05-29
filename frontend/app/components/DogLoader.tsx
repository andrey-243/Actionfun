"use client";

interface DogLoaderProps {
  text?: string;
}

const BODY = `M403.31,174.312c-0.048-0.278-11.065-141.195-11.065-141.195c-0.15-1.945-1.537-3.572-3.434-4.028
  c-1.895-0.455-3.872,0.361-4.891,2.026l-23.47,38.341l-15.485-30.824c-0.804-1.599-2.475-2.57-4.265-2.474
  c-1.786,0.097-3.346,1.243-3.973,2.919l-13.768,36.851c-2.015-0.33-4.05-0.496-6.085-0.496
  c-15.872,0-30.038,10.036-35.463,24.755l-61.888,6.991c-1.25,0.141-2.384,0.799-3.127,1.814
  c-0.743,1.016-1.026,2.295-0.783,3.529c8.219,41.596,40.378,70.571,80.557,73.02l-2.122,7.703l-53.063,9.195
  c8.766,14.065,13.839,30.659,13.839,48.418c0,4.971-4.029,9-9,9s-9-4.029-9-9c0-40.701-33.113-73.814-73.814-73.814
  c-24.132,0-46.79,11.854-60.612,31.709c-1.979,2.841-5.41,4.294-8.829,3.742c-3.417-0.555-6.214-3.021-7.191-6.342
  c-1.866-6.339-12.406-37.636-36.681-31.852l-4.65,1.207l15.555,64.411c1.913,9.14,4.519,17.973,7.754,26.453
  c-19.99-15.077-35.064-35.734-43.406-59.935C2.106,140.161,37.437,67.656,103.712,44.81
  c4.699-1.62,7.195-6.743,5.576-11.442c-1.62-4.699-6.743-7.195-11.442-5.576C22.188,53.873-18.146,136.644,7.935,212.303
  c12.634,36.65,38.784,66.187,73.633,83.168c0.536,0.261,1.078,0.505,1.616,0.759
  c31.387,50.35,87.244,82.392,148.106,82.389c9.855,0,19.846-0.841,29.88-2.579
  c45.925-7.958,86.003-33.325,112.85-71.426C400.867,266.513,411.269,220.237,403.31,174.312z`;

const SNOUT = `M80.496,90.201c-1.166-0.254-2.386-0.034-3.39,0.612c-1.004,0.646-1.711,1.664-1.964,2.83
  L61.61,155.932c10.687,1.554,20.29,7.56,28.296,17.837c3.208,4.118,5.798,8.494,7.838,12.544
  c17.11-17.291,40.567-27.269,65.268-27.269c23.548,0,45.051,8.915,61.32,23.542
  c-8.316-16.64-21.961-30.769-39.96-39.483c-11.975-5.797-24.746-8.736-37.957-8.736
  c-12.138,0-24.185,2.555-35.289,7.44C112.563,119.782,105.465,95.635,80.496,90.201z`;

export default function DogLoader({ text = "Analyse en cours…" }: DogLoaderProps) {
  return (
    <div style={{ display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center", padding:"40px 24px", gap:"20px" }}>
      <style>{`
        @keyframes dl-fwd { 0%{stroke-dashoffset:0} 100%{stroke-dashoffset:-1} }
        @keyframes dl-bwd { 0%{stroke-dashoffset:0} 100%{stroke-dashoffset:1}  }
        @keyframes dl-rot {
          0%  { transform: perspective(700px) rotateY(-18deg) rotateX(8deg)  rotateZ(-2deg); }
          25% { transform: perspective(700px) rotateY(14deg)  rotateX(-6deg) rotateZ(2deg);  }
          50% { transform: perspective(700px) rotateY(10deg)  rotateX(12deg) rotateZ(-3deg); }
          75% { transform: perspective(700px) rotateY(-8deg)  rotateX(-10deg) rotateZ(1deg); }
          100%{ transform: perspective(700px) rotateY(-18deg) rotateX(8deg)  rotateZ(-2deg); }
        }
        @keyframes dl-bar { 0%{transform:translateX(-100%)} 100%{transform:translateX(400%)} }
        .dl-rot { display:inline-block; animation: dl-rot 10s ease-in-out infinite; }
        .dl-fwd { animation: dl-fwd 5.5s linear infinite; }
        .dl-bwd { animation: dl-bwd 5.5s linear infinite; }
        .dl-bar { animation: dl-bar 2s ease-in-out infinite; }
      `}</style>

      <div className="dl-rot">
        <svg viewBox="0 0 405.918 405.918" width="160" height="160" fill="none" style={{ display:"block", overflow:"visible" }}>
          <path className="dl-fwd" d={BODY}  stroke="#ccc" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" pathLength="1" strokeDasharray="0.18 0.82" />
          <path className="dl-bwd" d={BODY}  stroke="#555" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" pathLength="1" strokeDasharray="0.18 0.82" />
          <path className="dl-fwd" d={SNOUT} stroke="#ccc" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" pathLength="1" strokeDasharray="0.18 0.82" />
          <path className="dl-bwd" d={SNOUT} stroke="#555" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" pathLength="1" strokeDasharray="0.18 0.82" />
        </svg>
      </div>

      <span style={{ fontSize:"13px", color:"var(--color-text-muted)", letterSpacing:"0.02em" }}>
        {text}
      </span>

      <div style={{ width:"140px", height:"2px", background:"var(--color-border)", borderRadius:"99px", overflow:"hidden", position:"relative" }}>
        <div className="dl-bar" style={{ position:"absolute", left:0, top:0, width:"25%", height:"100%", background:"var(--color-text-muted)", borderRadius:"99px", transform:"translateX(-100%)" }} />
      </div>
    </div>
  );
}

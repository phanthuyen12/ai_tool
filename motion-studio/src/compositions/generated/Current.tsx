import React from 'react';
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  Easing,
} from 'remotion';

type Candle = {
  o: number;
  c: number;
  h: number;
  l: number;
  t: number; // birth time in frames
};

const CREAM = '#f3efe0';
const GRID = 'rgba(243,239,224,0.06)';
const GREEN = '#2fbf8f';
const RED = '#e8827a';

// Deterministic pseudo-random
const rand = (seed: number) => {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

const buildCandles = (): Candle[] => {
  const candles: Candle[] = [];
  let price = 0.5;
  let seed = 1;
  // rally up
  for (let i = 0; i < 14; i++) {
    seed++;
    const up = rand(seed) > 0.28;
    const body = 0.012 + rand(seed + 7) * 0.02;
    const o = price;
    const c = up ? price + body : price - body * 0.7;
    const h = Math.max(o, c) + 0.008 + rand(seed + 3) * 0.012;
    const l = Math.min(o, c) - 0.008 - rand(seed + 5) * 0.012;
    candles.push({ o, c, h, l, t: 20 + i * 22 });
    price = c;
  }
  // sweep high then reversal down
  for (let i = 0; i < 16; i++) {
    seed++;
    const down = rand(seed) > 0.22;
    const body = 0.014 + rand(seed + 7) * 0.024;
    const o = price;
    const c = down ? price - body : price + body * 0.6;
    const h = Math.max(o, c) + 0.008 + rand(seed + 3) * 0.014;
    const l = Math.min(o, c) - 0.008 - rand(seed + 5) * 0.014;
    candles.push({ o, c, h, l, t: 330 + i * 24 });
    price = c;
  }
  return candles;
};

const ALL_CANDLES = buildCandles();

// chart mapping
const CHART_X = 90;
const CHART_Y = 150;
const CHART_W = 900;
const CHART_H = 620;

const priceToY = (p: number) => {
  // p in [0,1] roughly; invert
  return CHART_Y + CHART_H - p * CHART_H;
};

const candleX = (index: number) => {
  const step = CHART_W / 34;
  return CHART_X + 20 + index * step;
};

const Candle: React.FC<{ c: Candle; x: number; progress: number }> = ({ c, x, progress }) => {
  const bull = c.c >= c.o;
  const color = bull ? GREEN : RED;
  const bodyTop = priceToY(Math.max(c.o, c.c));
  const bodyBot = priceToY(Math.min(c.o, c.c));
  const bodyH = Math.max(2, bodyBot - bodyTop);
  const wickTop = priceToY(c.h);
  const wickBot = priceToY(c.l);
  const w = 12;
  const grow = interpolate(progress, [0, 1], [0, 1], { extrapolateRight: 'clamp' });
  const curBodyTop = interpolate(grow, [0, 1], [wickTop, bodyTop]);
  const curBodyBot = interpolate(grow, [0, 1], [wickTop, bodyBot]);
  return (
    <g opacity={grow}>
      <line x1={x} y1={wickTop} x2={x} y2={wickBot} stroke={color} strokeWidth={2} />
      <rect
        x={x - w / 2}
        y={curBodyTop}
        width={w}
        height={Math.max(2, curBodyBot - curBodyTop)}
        fill={color}
        rx={1}
      />
    </g>
  );
};

const Label: React.FC<{
  text: string;
  x: number;
  y: number;
  appear: number;
  color?: string;
  size?: number;
  anchor?: 'start' | 'middle' | 'end';
}> = ({ text, x, y, appear, color = CREAM, size = 20, anchor = 'start' }) => {
  const op = interpolate(appear, [0, 1], [0, 1], { extrapolateRight: 'clamp' });
  const dy = interpolate(appear, [0, 1], [10, 0], { extrapolateRight: 'clamp' });
  return (
    <text
      x={x}
      y={y + dy}
      fill={color}
      fontSize={size}
      fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
      textAnchor={anchor}
      opacity={op}
      letterSpacing={1}
    >
      {text}
    </text>
  );
};

export const SMCSmartMoneyScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const t = (sec: number) => sec * fps;

  // slow camera drift
  const driftX = Math.sin(frame / 90) * 6;
  const driftY = Math.cos(frame / 110) * 5;
  const driftScale = 1 + Math.sin(frame / 200) * 0.004;

  // BSL line reveal
  const bslAppear = interpolate(frame, [t(2.0), t(3.2)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // Order block reveal
  const obAppear = interpolate(frame, [t(9.5), t(11.0)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // LTF inducement line
  const indAppear = interpolate(frame, [t(15.0), t(16.5)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // Lens lines + inset
  const insetAppear = interpolate(frame, [t(18.0), t(20.0)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // POI label
  const poiAppear = interpolate(frame, [t(21.0), t(22.5)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // diamond marker
  const diamondAppear = interpolate(frame, [t(13.0), t(14.0)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.back(2)),
  });

  // caption fade
  const captionAppear = interpolate(frame, [t(1.0), t(2.5)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // header
  const headerAppear = interpolate(frame, [0, t(0.8)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // grid pulse
  const gridPulse = 0.5 + 0.5 * Math.sin(frame / 40);

  // BSL price level (the sweep high)
  const bslPrice = 0.98;

  // Order block region: around swing low cluster before rally
  const obTop = priceToY(0.62);
  const obBot = priceToY(0.48);
  const obX = candleX(2) - 14;
  const obW = candleX(6) - candleX(2) + 28;

  // Inducement region: small region near recent price action
  const indPrice = 0.72;
  const indY = priceToY(indPrice);
  const indX1 = candleX(20);
  const indX2 = candleX(28);

  // Diamond at sweep wick (highest high)
  let sweepIdx = 0;
  let sweepHigh = -Infinity;
  ALL_CANDLES.forEach((c, i) => {
    if (c.h > sweepHigh) {
      sweepHigh = c.h;
      sweepIdx = i;
    }
  });
  const diamondX = candleX(sweepIdx);
  const diamondY = priceToY(sweepHigh) - 14;

  // Inset box
  const insetW = 380;
  const insetH = 300;
  const insetX = 600;
  const insetY = 700;

  // Zoom lens lines from small region to inset corners
  const regionX1 = candleX(22);
  const regionX2 = candleX(30);
  const regionY1 = priceToY(0.85);
  const regionY2 = priceToY(0.55);

  // Inset candles (granular)
  const insetCandles = React.useMemo(() => {
    const arr: { o: number; c: number; h: number; l: number }[] = [];
    let p = 0.5;
    for (let i = 0; i < 18; i++) {
      const s = 100 + i;
      const up = rand(s) > 0.45;
      const body = 0.03 + rand(s + 3) * 0.05;
      const o = p;
      const c = up ? p + body : p - body;
      const h = Math.max(o, c) + 0.02 + rand(s + 5) * 0.03;
      const l = Math.min(o, c) - 0.02 - rand(s + 7) * 0.03;
      arr.push({ o, c, h, l });
      p = c;
    }
    return arr;
  }, []);

  const insetStep = (insetW - 40) / insetCandles.length;
  const insetPriceToY = (p: number) => {
    const min = 0.2;
    const max = 1.1;
    const norm = (p - min) / (max - min);
    return insetY + insetH - 30 - norm * (insetH - 60);
  };

  // POI rect inside inset
  const poiTop = insetPriceToY(0.85);
  const poiBot = insetPriceToY(0.6);

  return (
    <AbsoluteFill style={{ backgroundColor: '#1b1f14' }}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ display: 'block' }}
      >
        <defs>
          <radialGradient id="vignette" cx="50%" cy="45%" r="70%">
            <stop offset="0%" stopColor="rgba(0,0,0,0)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0.45)" />
          </radialGradient>
        </defs>

        <g
          transform={`translate(${driftX}, ${driftY}) scale(${driftScale})`}
          style={{ transformOrigin: 'center' }}
        >
          {/* faint olive grid */}
          <g opacity={0.5 + gridPulse * 0.15}>
            {Array.from({ length: 9 }).map((_, i) => (
              <line
                key={`h${i}`}
                x1={CHART_X}
                y1={CHART_Y + (CHART_H / 8) * i}
                x2={CHART_X + CHART_W}
                y2={CHART_Y + (CHART_H / 8) * i}
                stroke={GRID}
                strokeWidth={1}
              />
            ))}
            {Array.from({ length: 13 }).map((_, i) => (
              <line
                key={`v${i}`}
                x1={CHART_X + (CHART_W / 12) * i}
                y1={CHART_Y}
                x2={CHART_X + (CHART_W / 12) * i}
                y2={CHART_Y + CHART_H}
                stroke={GRID}
                strokeWidth={1}
              />
            ))}
          </g>

          {/* BSL dashed line */}
          <g opacity={bslAppear}>
            <line
              x1={CHART_X}
              y1={priceToY(bslPrice)}
              x2={CHART_X + CHART_W}
              y2={priceToY(bslPrice)}
              stroke={CREAM}
              strokeWidth={1.5}
              strokeDasharray="8 8"
              opacity={0.8}
            />
            <Label
              text="BSL"
              x={CHART_X + CHART_W - 8}
              y={priceToY(bslPrice) - 12}
              appear={bslAppear}
              anchor="end"
              size={22}
            />
          </g>

          {/* Order block zone */}
          <g opacity={obAppear}>
            <rect
              x={obX}
              y={obTop}
              width={obW}
              height={obBot - obTop}
              fill="rgba(196,160,110,0.22)"
              stroke="rgba(196,160,110,0.6)"
              strokeWidth={1.5}
            />
            <Label
              text="ORDER BLOCK"
              x={obX + obW / 2}
              y={obTop - 12}
              appear={obAppear}
              anchor="middle"
              size={20}
            />
          </g>

          {/* Candles */}
          {ALL_CANDLES.map((c, i) => {
            const p = interpolate(frame, [c.t, c.t + 14], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
              easing: Easing.out(Easing.cubic),
            });
            if (p <= 0) return null;
            return <Candle key={i} c={c} x={candleX(i)} progress={p} />;
          })}

          {/* Diamond marker at sweep */}
          <g
            opacity={diamondAppear}
            transform={`translate(${diamondX}, ${diamondY}) rotate(45)`}
          >
            <rect x={-8} y={-8} width={16} height={16} fill={CREAM} />
          </g>

          {/* LTF Inducement dashed line */}
          <g opacity={indAppear}>
            <line
              x1={indX1}
              y1={indY}
              x2={indX2}
              y2={indY}
              stroke={CREAM}
              strokeWidth={1.5}
              strokeDasharray="6 6"
              opacity={0.9}
            />
            <Label
              text="LTF Inducement"
              x={indX2 + 10}
              y={indY + 6}
              appear={indAppear}
              size={18}
            />
          </g>

          {/* Zoom lens converging lines */}
          <g opacity={insetAppear}>
            <line
              x1={regionX1}
              y1={regionY1}
              x2={insetX}
              y2={insetY}
              stroke={CREAM}
              strokeWidth={1}
              opacity={0.5}
            />
            <line
              x1={regionX2}
              y1={regionY2}
              x2={insetX + insetW}
              y2={insetY}
              stroke={CREAM}
              strokeWidth={1}
              opacity={0.5}
            />
            <line
              x1={regionX1}
              y1={regionY1}
              x2={insetX}
              y2={insetY + insetH}
              stroke={CREAM}
              strokeWidth={1}
              opacity={0.5}
            />
            <line
              x1={regionX2}
              y1={regionY2}
              x2={insetX + insetW}
              y2={insetY + insetH}
              stroke={CREAM}
              strokeWidth={1}
              opacity={0.5}
            />
            <rect
              x={regionX1}
              y={regionY1}
              width={regionX2 - regionX1}
              height={regionY2 - regionY1}
              fill="none"
              stroke={CREAM}
              strokeWidth={1.5}
              strokeDasharray="4 4"
              opacity={0.7}
            />
          </g>

          {/* Inset box */}
          <g opacity={insetAppear}>
            <rect
              x={insetX}
              y={insetY}
              width={insetW}
              height={insetH}
              fill="rgba(27,31,20,0.92)"
              stroke={CREAM}
              strokeWidth={1.5}
              rx={4}
            />
            {/* inset grid */}
            {Array.from({ length: 5 }).map((_, i) => (
              <line
                key={`ih${i}`}
                x1={insetX + 10}
                y1={insetY + 20 + ((insetH - 40) / 4) * i}
                x2={insetX + insetW - 10}
                y2={insetY + 20 + ((insetH - 40) / 4) * i}
                stroke={GRID}
                strokeWidth={1}
              />
            ))}
            {Array.from({ length: 7 }).map((_, i) => (
              <line
                key={`iv${i}`}
                x1={insetX + 20 + ((insetW - 40) / 6) * i}
                y1={insetY + 10}
                x2={insetX + 20 + ((insetW - 40) / 6) * i}
                y2={insetY + insetH - 10}
                stroke={GRID}
                strokeWidth={1}
              />
            ))}

            {/* inset candles */}
            {insetCandles.map((c, i) => {
              const x = insetX + 20 + i * insetStep + insetStep / 2;
              const bull = c.c >= c.o;
              const color = bull ? GREEN : RED;
              const bt = insetPriceToY(Math.max(c.o, c.c));
              const bb = insetPriceToY(Math.min(c.o, c.c));
              const wt = insetPriceToY(c.h);
              const wb = insetPriceToY(c.l);
              const appear = interpolate(
                frame,
                [t(18.5) + i * 2, t(18.5) + i * 2 + 10],
                [0, 1],
                { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
              );
              return (
                <g key={i} opacity={appear}>
                  <line x1={x} y1={wt} x2={x} y2={wb} stroke={color} strokeWidth={1.5} />
                  <rect
                    x={x - 5}
                    y={bt}
                    width={10}
                    height={Math.max(2, bb - bt)}
                    fill={color}
                    rx={1}
                  />
                </g>
              );
            })}

            {/* POI rect */}
            <g opacity={poiAppear}>
              <rect
                x={insetX + 15}
                y={poiTop}
                width={insetW - 30}
                height={poiBot - poiTop}
                fill="rgba(140,170,130,0.22)"
                stroke="rgba(140,170,130,0.7)"
                strokeWidth={1.5}
              />
              <Label
                text="LTF POI"
                x={insetX + insetW / 2}
                y={poiTop - 10}
                appear={poiAppear}
                anchor="middle"
                size={18}
              />
            </g>
          </g>

          {/* viewfinder corner brackets */}
          {[
            { x: 30, y: 30, sx: 1, sy: 1 },
            { x: width - 30, y: 30, sx: -1, sy: 1 },
            { x: 30, y: height - 30, sx: 1, sy: -1 },
            { x: width - 30, y: height - 30, sx: -1, sy: -1 },
          ].map((c, i) => (
            <g key={i} stroke={CREAM} strokeWidth={2} fill="none" opacity={0.85}>
              <line x1={c.x} y1={c.y} x2={c.x + 40 * c.sx} y2={c.y} />
              <line x1={c.x} y1={c.y} x2={c.x} y2={c.y + 40 * c.sy} />
            </g>
          ))}

          {/* bottom timecode ticks */}
          {Array.from({ length: 21 }).map((_, i) => {
            const x = 90 + i * ((width - 180) / 20);
            const big = i % 5 === 0;
            return (
              <line
                key={i}
                x1={x}
                y1={height - 60}
                x2={x}
                y2={height - 60 + (big ? 12 : 6)}
                stroke={CREAM}
                strokeWidth={1}
                opacity={0.5}
              />
            );
          })}

          {/* header */}
          <g opacity={headerAppear}>
            <rect x={90} y={70} width={16} height={16} fill={CREAM} />
            <text
              x={118}
              y={84}
              fill={CREAM}
              fontSize={20}
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              letterSpacing={1}
            >
              30m timeframe
            </text>
          </g>

          {/* caption */}
          <text
            x={width / 2}
            y={height - 30}
            fill="#8a8a80"
            fontSize={16}
            fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
            textAnchor="middle"
            opacity={captionAppear}
            letterSpacing={0.5}
          >
            Mo phong bieu do SMC bang code - khong phai tin hieu giao dich that.
          </text>
        </g>

        <rect width={width} height={height} fill="url(#vignette)" pointerEvents="none" />
      </svg>
    </AbsoluteFill>
  );
};

export default SMCSmartMoneyScene;

export const durationInSeconds = 32;
export const fps = 30;
export const width = 1080;
export const height = 1080;

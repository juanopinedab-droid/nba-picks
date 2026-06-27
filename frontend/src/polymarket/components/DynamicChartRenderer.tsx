import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer
} from 'recharts'

const SERIES_COLORS = ['#38bdf8', '#f472b6', '#a78bfa', '#34d399', '#fb923c']
const DONUT_COLORS = [
  '#38bdf8', '#818cf8', '#34d399', '#f472b6', '#fb923c',
  '#a78bfa', '#fbbf24', '#f87171'
]

interface BaseChartConfig {
  type: 'line_chart' | 'bar_chart' | 'donut_chart' | 'depth_chart'
  title: string
}

interface LineChartConfig extends BaseChartConfig {
  type: 'line_chart'
  xAxisLabel?: string
  yAxisLabel?: string
  data: Record<string, string | number>[]
}

interface BarChartConfig extends BaseChartConfig {
  type: 'bar_chart'
  xAxisLabel?: string
  data: Record<string, string | number>[]
}

interface DonutChartConfig extends BaseChartConfig {
  type: 'donut_chart'
  data: { label: string; value: number }[]
}

interface DepthChartConfig extends BaseChartConfig {
  type: 'depth_chart'
  data: { price: number; bid_size: number; ask_size: number }[]
}

export type ChartConfig = LineChartConfig | BarChartConfig | DonutChartConfig | DepthChartConfig

interface DynamicChartRendererProps {
  charts: ChartConfig[]
}

const tooltipStyle = {
  background: '#0f172a',
  border: '1px solid #334155',
  fontSize: 11,
  borderRadius: 6,
}

function extractSeriesKeys(data: Record<string, string | number>[]): string[] {
  if (!data || data.length === 0) return []
  return Object.keys(data[0]).filter(k => k !== 'name')
}

function renderLineChart(chart: LineChartConfig) {
  const seriesKeys = extractSeriesKeys(chart.data)
  return (
    <LineChart data={chart.data}>
      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
      <XAxis dataKey="name" tick={{ fontSize: 10 }} stroke="#64748b" />
      <YAxis tick={{ fontSize: 10 }} stroke="#64748b" />
      <Tooltip contentStyle={tooltipStyle} />
      <Legend wrapperStyle={{ fontSize: 10 }} />
      {seriesKeys.map((key, i) => (
        <Line
          key={key}
          type="monotone"
          dataKey={key}
          stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
          strokeWidth={2}
          dot={{ r: 2 }}
        />
      ))}
    </LineChart>
  )
}

function renderBarChart(chart: BarChartConfig) {
  const seriesKeys = extractSeriesKeys(chart.data)
  return (
    <BarChart data={chart.data}>
      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
      <XAxis dataKey="name" tick={{ fontSize: 10 }} stroke="#64748b" />
      <YAxis tick={{ fontSize: 10 }} stroke="#64748b" />
      <Tooltip contentStyle={tooltipStyle} />
      <Legend wrapperStyle={{ fontSize: 10 }} />
      {seriesKeys.map((key, i) => (
        <Bar
          key={key}
          dataKey={key}
          fill={SERIES_COLORS[i % SERIES_COLORS.length]}
          radius={[2, 2, 0, 0]}
        />
      ))}
    </BarChart>
  )
}

function renderDonutChart(chart: DonutChartConfig) {
  return (
    <PieChart>
      <Pie
        data={chart.data}
        dataKey="value"
        nameKey="label"
        cx="50%"
        cy="50%"
        innerRadius={48}
        outerRadius={80}
        paddingAngle={2}
      >
        {chart.data.map((_, i) => (
          <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} stroke="transparent" />
        ))}
      </Pie>
      <Tooltip contentStyle={tooltipStyle} />
      <Legend wrapperStyle={{ fontSize: 10 }} />
    </PieChart>
  )
}

function renderDepthChart(chart: DepthChartConfig) {
  const sorted = [...chart.data].sort((a, b) => a.price - b.price)
  return (
    <AreaChart data={sorted}>
      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
      <XAxis
        dataKey="price"
        tick={{ fontSize: 10 }}
        stroke="#64748b"
        tickFormatter={(v: number) => `$${v.toFixed(2)}`}
      />
      <YAxis tick={{ fontSize: 10 }} stroke="#64748b" />
      <Tooltip contentStyle={tooltipStyle} />
      <Legend wrapperStyle={{ fontSize: 10 }} />
      <Area
        type="stepAfter"
        dataKey="bid_size"
        stackId="1"
        stroke="#34d399"
        fill="#34d399"
        fillOpacity={0.25}
        name="Bids"
      />
      <Area
        type="stepAfter"
        dataKey="ask_size"
        stackId="2"
        stroke="#f472b6"
        fill="#f472b6"
        fillOpacity={0.25}
        name="Asks"
      />
    </AreaChart>
  )
}

export function DynamicChartRenderer({ charts }: DynamicChartRendererProps) {
  if (!charts || charts.length === 0) return null

  return (
    <div className="space-y-4 mt-6 border-t border-border pt-4 animate-in fade-in slide-in-from-bottom-2 duration-500">
      <span className="text-xs font-semibold text-foreground">
        DATA VISUALIZATIONS
      </span>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {charts.map((chart, idx) => (
          <div
            key={idx}
            className="bg-background border border-border rounded-lg p-4"
          >
            <h4 className="text-xs font-medium text-foreground/80 mb-3">
              {chart.title}
            </h4>
            <ResponsiveContainer width="100%" height={220}>
              {chart.type === 'line_chart'
                ? renderLineChart(chart as LineChartConfig)
                : chart.type === 'bar_chart'
                  ? renderBarChart(chart as BarChartConfig)
                  : chart.type === 'donut_chart'
                    ? renderDonutChart(chart as DonutChartConfig)
                    : chart.type === 'depth_chart'
                      ? renderDepthChart(chart as DepthChartConfig)
                      : null}
            </ResponsiveContainer>
          </div>
        ))}
      </div>
    </div>
  )
}

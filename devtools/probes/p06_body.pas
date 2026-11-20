procedure Try1(Board: Variant; RX, RY, RZ, DZ: Double; Tag: String);
var Body, Model: Variant; R: TCoordRect;
begin
  Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
  Model := Body.ModelFactory_FromFilename('%ROOT%\testdata\models\model.step', False);
  Model.SetState(RX, RY, RZ, MilsToCoord(DZ));
  Body.Model := Model;
  Body.SetState_FromModel;
  Body.Layer := eTopLayer;
  Board.AddPCBObject(Body);
  R := Body.BoundingRectangle;
  L(Tag + ' at0: L=' + FloatToStr(CoordToMMs(R.Left)) + ' R=' + FloatToStr(CoordToMMs(R.Right)) + ' B=' + FloatToStr(CoordToMMs(R.Bottom)) + ' T=' + FloatToStr(CoordToMMs(R.Top)) + ' sh=' + FloatToStr(CoordToMMs(Body.StandoffHeight)) + ' oh=' + FloatToStr(CoordToMMs(Body.OverallHeight)));
  Body.MoveByXY(MMsToCoord(100), MMsToCoord(50));
  R := Body.BoundingRectangle;
  L(Tag + ' moved: L=' + FloatToStr(CoordToMMs(R.Left)) + ' R=' + FloatToStr(CoordToMMs(R.Right)) + ' B=' + FloatToStr(CoordToMMs(R.Bottom)) + ' T=' + FloatToStr(CoordToMMs(R.Top)));
end;

procedure Body;
var
  Doc, Board: Variant;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  Try1(Board, 0, 0, 0, 0, 'r000');
  Try1(Board, 0, 0, 90, 0, 'rz90');
  Try1(Board, 90, 0, 0, 0, 'rx90');
  Try1(Board, 0, 90, 0, 0, 'ry90');
  Try1(Board, 90, 0, 90, 0, 'rx90z90');
  Try1(Board, 90, 90, 0, 0, 'rx90y90');
  Try1(Board, 0, 0, 0, 40, 'dz40');
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p06.PcbDoc');
  Doc.DoFileSave('');
  Client.CloseDocument(Doc);
end;
